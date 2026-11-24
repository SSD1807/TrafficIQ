"""FastAPI endpoints for live incidents, with mocked TomTom HTTP (no key, no internet)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from backend import main
from routing.osm_network import OSMNetworkProvider
from tests.incident_helpers import FakeTomTom, corridor_graph, incident, make_sync, node, optimizer_for, payload, row0_line

KEY = "SECRET-KEY-123"
A, B = str(node(0, 0)), str(node(0, 3))


def setup_api(monkeypatch, fake, *, key=KEY, **config):
    sync, svc, mgr, graph, clock = make_sync(fake, key=key, **config)
    provider = OSMNetworkProvider()
    provider.graph, provider.source = graph, "test_grid"
    opt = optimizer_for(graph, mgr)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "get_incidents", lambda: mgr)
    monkeypatch.setattr(main, "get_optimizer", lambda: opt)
    monkeypatch.setattr(main, "get_incident_sync", lambda: sync)
    monkeypatch.setattr(main, "get_tomtom_incidents", lambda: svc)
    return TestClient(main.app), mgr, clock


def test_refresh_imports_tomtom_incidents_and_reports_live_status(monkeypatch):
    client, mgr, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("C", icon=8), incident("U", icon=1, geometry_type="Point",
                                                                                            coords=[85.8245 + 1.5 * 0.004, 20.2961 + 0.002]))))
    r = client.post("/incidents/refresh")
    assert r.status_code == 200
    d = r.json()
    assert d["refreshed"] is True and d["throttled"] is False
    assert d["provider"]["state"] == "live" and d["provider"]["is_live"] is True and d["provider"]["incident_count"] == 2
    assert d["counts"]["tomtom"] == 2 and d["counts"]["tomtom_matched"] == 1 and d["counts"]["tomtom_unmatched"] == 1
    by_id = {e["provider_id"]: e for e in d["feed"]}
    assert by_id["C"]["status"] == "applied" and by_id["C"]["edges"][0]["road_name"] == "Corridor Road"
    assert by_id["U"]["status"] == "unmatched" and by_id["U"]["match"]["reason"] == "no_nearby_road"
    assert KEY not in r.text


def test_refresh_is_rate_limited_server_side(monkeypatch):
    fake = FakeTomTom(payload(incident("C")))
    client, _, _ = setup_api(monkeypatch, fake)
    assert client.post("/incidents/refresh").json()["refreshed"] is True
    again = client.post("/incidents/refresh").json()
    assert again["refreshed"] is False and again["throttled"] is True and again["counts"]["tomtom"] == 1
    assert len(fake.requests) == 1


def test_missing_key_reports_disabled_and_never_fabricates_incidents(monkeypatch):
    client, mgr, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("C"))), key="")
    mgr.add(node(1, 0), node(1, 1), source="manual", description="mine")
    d = client.post("/incidents/refresh").json()
    assert d["provider"]["state"] == "disabled_no_key" and d["provider"]["is_live"] is False and d["refreshed"] is False
    assert d["counts"] == {**d["counts"], "tomtom": 0, "manual": 1}
    assert [e["source"] for e in d["feed"]] == ["manual"]
    st = client.get("/traffic/tomtom/incidents/status").json()
    assert st["state"] == "disabled_no_key" and st["configured"] is False


def test_api_failure_keeps_cached_incidents_and_reports_cached_state(monkeypatch):
    client, mgr, clock = setup_api(monkeypatch, FakeTomTom(payload(incident("C")), httpx.Response(503)))
    client.post("/incidents/refresh")
    clock.advance(61)
    d = client.post("/incidents/refresh").json()
    assert d["refreshed"] is False and d["provider"]["state"] == "cached" and d["provider"]["using_cached_data"] is True
    assert d["provider"]["last_error"]["type"] == "server_error" and d["counts"]["tomtom"] == 1
    assert client.get("/incidents").json()["counts"]["tomtom"] == 1


def test_unavailable_state_when_first_request_fails(monkeypatch):
    client, _, _ = setup_api(monkeypatch, FakeTomTom(httpx.Response(403)))
    d = client.post("/incidents/refresh").json()
    assert d["provider"]["state"] == "unavailable" and d["feed"] == [] and "API key" in d["provider"]["message"]
    assert d["provider"]["last_error"]["http_status"] == 403


def test_live_but_empty_feed_is_a_valid_status(monkeypatch):
    client, _, _ = setup_api(monkeypatch, FakeTomTom(payload()))
    d = client.post("/incidents/refresh").json()
    assert d["provider"]["state"] == "live_empty" and d["provider"]["is_live"] is True and d["feed"] == []


def test_get_incidents_keeps_legacy_shape_and_adds_feed(monkeypatch):
    client, mgr, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("C"))))
    client.post("/incidents/refresh")
    d = client.get("/incidents").json()
    assert set(d) >= {"incidents", "source_types", "feed", "counts", "provider", "data_notice"}
    assert d["source_types"] == ["manual", "simulation", "tomtom"]
    assert d["incidents"][0]["source"] == "tomtom" and d["incidents"][0]["id"].startswith("tomtom:C:")
    assert "synthetic" not in d["data_notice"].lower() or "test data" in d["data_notice"].lower()


def test_tomtom_incidents_endpoint_returns_provider_view(monkeypatch):
    client, _, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("C"), incident("N", icon=1, geometry_type="Point", coords=[77.0, 28.0]))))
    d = client.get("/traffic/tomtom/incidents?refresh=true").json()
    assert d["matched"] == 1 and d["unmatched"] == 1 and {e["source"] for e in d["incidents"]} == {"tomtom"}
    assert d["provider"]["state"] == "live"


def test_manual_incident_create_and_delete_still_work_and_tomtom_is_protected(monkeypatch):
    client, mgr, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("C"))))
    created = client.post("/incidents", json={"edge_u": A, "edge_v": str(node(0, 1)), "kind": "accident", "severity": 0.7,
                                              "source": "manual", "description": "test accident"})
    assert created.status_code == 201 and created.json()["source"] == "manual"
    assert client.post("/incidents", json={"edge_u": A, "edge_v": str(node(0, 1)), "source": "tomtom"}).status_code == 422
    assert client.post("/incidents", json={"edge_u": A, "edge_v": B}).status_code == 400   # not a directed edge of the graph
    client.post("/incidents/refresh")
    tomtom_id = next(i["id"] for i in client.get("/incidents").json()["incidents"] if i["source"] == "tomtom")
    assert client.delete(f"/incidents/{tomtom_id}").status_code == 409
    assert client.delete("/incidents?source=tomtom").status_code == 400
    assert client.delete(f"/incidents/{created.json()['id']}").status_code == 200
    assert client.delete(f"/incidents/{created.json()['id']}").status_code == 404
    client.post("/incidents", json={"edge_u": A, "edge_v": str(node(0, 1)), "source": "simulation"})
    assert client.delete("/incidents").json()["removed_count"] == 1                       # clears manual+simulation only
    assert client.get("/incidents").json()["counts"]["tomtom"] == 1


def test_route_calculation_uses_active_matched_incidents(monkeypatch):
    client, mgr, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("C", icon=8))))
    body = {"origin_node": A, "destination_node": B, "algorithm": "dijkstra", "alternatives": 2, "use_simulated_congestion": False}
    before = client.post("/routes/calculate", json=body).json()
    client.post("/incidents/refresh")
    after = client.post("/routes/calculate", json=body).json()
    assert before["routes"][0]["node_sequence"] == [str(node(0, c)) for c in range(4)]
    assert after["routes"][0]["node_sequence"] != before["routes"][0]["node_sequence"] and after["active_incident_count"] == 1
    assert after["incident_summary"]["applied"]["tomtom_applied_to_routing"] == 1 and after["incident_summary"]["on_best_route"] == []


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_incident_impact_endpoint_returns_alternative_route(monkeypatch, algorithm):
    client, _, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("C", icon=8))))
    client.post("/incidents/refresh")
    d = client.post("/routes/incident-impact", json={"origin_node": A, "destination_node": B, "algorithm": algorithm,
                                                     "use_simulated_congestion": False}).json()
    assert d["rerouted"] is True and d["delta"]["travel_time_min"] > 0
    assert d["incidents_on_original_route"][0]["provider_id"] == "C" and d["incidents_on_original_route"][0]["kind"] == "road_closure"
    assert d["with_incidents"]["routes"][0]["node_sequence"] != d["without_live_incidents"]["routes"][0]["node_sequence"]
    assert d["provider"]["state"] == "live"


def test_incident_impact_when_live_closures_block_every_route(monkeypatch):
    from tests.incident_helpers import LAT0, LON0, M_LAT, STEP
    north = [[LON0 + 3 * 0.00001, LAT0 + STEP * i / 5] for i in range(6)]          # Cross Street 0, northbound (1000 -> 1010)
    client, mgr, _ = setup_api(monkeypatch, FakeTomTom(payload(incident("E", icon=8, coords=row0_line(0.0, 1.0)),
                                                              incident("N", icon=8, coords=north))))
    client.post("/incidents/refresh")
    assert client.get("/incidents").json()["counts"]["tomtom_applied_to_routing"] == 2
    d = client.post("/routes/incident-impact", json={"origin_node": A, "destination_node": B, "use_simulated_congestion": False}).json()
    assert d["rerouted"] is False and d["with_incidents"]["routes"] == [] and "blocked" in d["reason"].lower()
    assert len(d["incidents_on_original_route"]) >= 1 and "No drivable route" in d["with_incidents"]["error"]


def test_manual_closures_that_block_everything_return_404_like_before(monkeypatch):
    client, mgr, _ = setup_api(monkeypatch, FakeTomTom(payload()))
    for u, v in [(node(0, 0), node(0, 1)), (node(0, 0), node(1, 0))]:
        mgr.add(u, v, kind="road_closure", severity=1.0, source="manual")
    r = client.post("/routes/incident-impact", json={"origin_node": A, "destination_node": B, "use_simulated_congestion": False})
    assert r.status_code == 404


def test_health_reports_incident_provider_without_calling_out_or_leaking(monkeypatch):
    fake = FakeTomTom(payload(incident("C")))
    client, _, _ = setup_api(monkeypatch, fake)
    r = client.get("/health")
    assert r.status_code == 200 and fake.requests == [] and KEY not in r.text
    assert "tomtom_incidents" in r.json()["data_sources"]


def test_invalid_incident_payloads_are_rejected(monkeypatch):
    client, _, _ = setup_api(monkeypatch, FakeTomTom(payload()))
    assert client.post("/incidents", json={"edge_u": A, "edge_v": str(node(0, 1)), "severity": 2}).status_code == 422
    assert client.post("/incidents", json={"edge_u": A, "edge_v": str(node(0, 1)), "kind": "ufo"}).status_code == 422
    assert client.delete("/incidents?source=bogus").status_code == 400
