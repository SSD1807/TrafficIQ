"""API-level checks for the provider-based traffic endpoints (HTTP to HERE is mocked; nothing here proves live coverage)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend import main
from routing.incident_manager import IncidentManager
from routing.osm_network import OSMNetworkProvider, build_synthetic_fallback_graph
from routing.route_optimizer import RouteOptimizer
from services.traffic_providers import create_provider
from services.traffic_providers.here_provider import HereProvider
from tests.test_here_provider import Clock, Resp, eta_body, flow_body

KEY = "SECRET-API-KEY-XYZ"


class UrlRoutedClient:
    """Answers by URL: flow / router / tile."""
    def __init__(self, tile=b"\x89PNG-bytes", flow=None, eta=None):
        self.tile, self.flow, self.eta, self.calls = tile, flow, eta, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if "/v7/flow" in url: return Resp(200, self.flow if self.flow is not None else flow_body())
        if "/v8/routes" in url: return Resp(200, self.eta if self.eta is not None else eta_body())
        return Resp(200, None, content=self.tile)


def _setup(monkeypatch, provider):
    gp = OSMNetworkProvider(); gp.graph = build_synthetic_fallback_graph(); gp.source = "synthetic_fallback"
    incidents = IncidentManager(); optimizer = RouteOptimizer(gp.graph, gp.source, incidents)
    monkeypatch.setattr(main, "get_provider", lambda: gp)
    monkeypatch.setattr(main, "get_incidents", lambda: incidents)
    monkeypatch.setattr(main, "get_optimizer", lambda: optimizer)
    monkeypatch.setattr(main, "get_traffic_provider", lambda: provider)
    return TestClient(main.app)


def here(client=None, key=KEY):
    return HereProvider(api_key=key, client=client or UrlRoutedClient(), clock=Clock())


def test_provider_status_without_key_is_disabled_and_has_no_secrets(monkeypatch):
    client = _setup(monkeypatch, here(key=""))
    body = client.get("/traffic/provider/status").json()
    assert body["provider"] == "here" and body["configured"] is False and body["state"] == "disabled"
    assert body["coverage_observed"] is False


def test_flow_endpoint_live_then_cached_labels_and_no_key_leak(monkeypatch):
    client = _setup(monkeypatch, here())
    first = client.get("/traffic/flow").json()
    assert first["data_state"] == "live" and first["is_live"] is True and first["segment_count"] == 1
    assert first["segments"][0]["level"] == "severe" and KEY not in str(first)
    second = client.get("/traffic/flow").json()
    assert second["data_state"] == "cached" and second["is_live"] is False
    assert client.get("/traffic/provider/status").json()["coverage_observed"] is True


def test_flow_endpoint_missing_key_and_invalid_bbox(monkeypatch):
    client = _setup(monkeypatch, here(key=""))
    body = client.get("/traffic/flow").json()
    assert body["data_state"] == "disabled" and body["is_live"] is False and body["segments"] == []
    assert client.get("/traffic/flow", params={"min_lon": 86, "max_lon": 85}).status_code == 400


def test_tile_proxy_labels_state_and_returns_empty_when_unavailable(monkeypatch):
    client = _setup(monkeypatch, here())
    r = client.get("/traffic/tiles/12/3000/1800.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["x-traffic-data-state"] == "live"
    assert r.content == b"\x89PNG-bytes" and KEY not in str(r.headers)
    client = _setup(monkeypatch, here(key=""))
    r = client.get("/traffic/tiles/12/3000/1800.png")
    assert r.status_code == 204 and r.headers["x-traffic-data-state"] == "disabled"


def test_health_reports_provider_state_without_external_request(monkeypatch):
    c = UrlRoutedClient(); client = _setup(monkeypatch, here(c))
    body = client.get("/health").json()
    assert body["data_sources"]["traffic_provider"]["provider"] == "here" and KEY not in str(body) and not c.calls


def test_route_calculate_with_live_traffic_labels_everything(monkeypatch):
    client = _setup(monkeypatch, here())
    r = client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8", "alternatives": 2, "use_live_traffic": True})
    assert r.status_code == 200
    data = r.json(); info = data["traffic_flow"]
    assert info["provider"] == "here" and info["status"] in {"applied", "no_edge_match"} and info["traffic_data_state"] == "live"
    assert info["is_live"] is (info["status"] == "applied")
    eta = data["provider_eta"]
    assert eta["reference_only"] is True and eta["data_state"] == "live" and eta["traffic_delay_min"] == 10.0
    assert data["routes"]  # OSM routes are always returned


def test_route_calculate_with_live_traffic_and_no_key_falls_back(monkeypatch):
    client = _setup(monkeypatch, here(key=""))
    plain = client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8", "alternatives": 2}).json()
    r = client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8", "alternatives": 2, "use_live_traffic": True})
    assert r.status_code == 200
    data = r.json()
    assert data["traffic_flow"]["status"] == "disabled" and data["traffic_flow"]["is_live"] is False
    assert data["provider_eta"]["data_state"] == "disabled"
    assert data["routes"][0]["node_sequence"] == plain["routes"][0]["node_sequence"]
    assert "live" not in data["traffic_cost_mode"]


def test_none_provider_endpoints(monkeypatch):
    client = _setup(monkeypatch, create_provider("none"))
    assert client.get("/traffic/provider/status").json()["state"] == "disabled"
    assert client.get("/traffic/flow").json()["data_state"] == "disabled"
    assert client.get("/traffic/tiles/10/1/1.png").status_code == 204
