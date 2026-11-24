"""HTTP-level tests for the new endpoints (network status, location search, /routes/plan, enriched incident feed).

They run on the 8-node synthetic graph that the existing API tests inject (clearly labelled test data), so they need no
internet and no OSM cache. Real-OSM behaviour is covered by test_incident_engine.py / test_route_planning.py.
"""
from fastapi.testclient import TestClient

from backend import main
from routing.osm_network import NetworkUnavailableError
from tests.test_api import _offline_dependencies


class _Provider:
    def __init__(self, state="live"):
        self._state = state

    def status(self):
        return {"state": self._state, "provider": "tomtom"}


class _Sync:
    def __init__(self, state="live"):
        self.provider = _Provider(state)
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1
        return {"refreshed": True}


def _client(monkeypatch, state="live"):
    provider, incidents, optimizer = _offline_dependencies(monkeypatch)
    sync = _Sync(state)
    monkeypatch.setattr(main, "get_incident_sync", lambda: sync)
    main._LABELERS.clear()
    import routing.route_optimizer as ro
    ro._GEOCODERS.clear()
    return TestClient(main.app), provider, incidents, optimizer, sync


def test_network_status_never_claims_synthetic_data_is_openstreetmap(monkeypatch):
    client, *_ = _client(monkeypatch)
    body = client.get("/network/status").json()
    assert body["is_real_osm"] is False and body["state"] == "not_openstreetmap" and body["source"] == "synthetic_fallback"


def test_missing_road_network_is_a_503_with_reason_and_hint(monkeypatch):
    client, *_ = _client(monkeypatch)

    def boom():
        raise NetworkUnavailableError("No cached OpenStreetMap road network exists.", "Run scripts\\download_osm.bat", code="road_network_not_downloaded")

    monkeypatch.setattr(main, "get_optimizer", boom)
    for call in (lambda: client.get("/routing/nodes"), lambda: client.post("/routes/plan", json={"origin_node": "1", "destination_node": "8"})):
        r = call()
        assert r.status_code == 503
        detail = r.json()["detail"]
        assert detail["code"] == "road_network_not_downloaded" and detail["reason"] and detail["hint"]


def test_routing_nodes_have_readable_labels_and_keep_osm_ids(monkeypatch):
    client, provider, *_ = _client(monkeypatch)
    nodes = client.get("/routing/nodes").json()["nodes"]
    assert nodes and {"id", "name", "label", "latitude", "longitude", "label_source"} <= set(nodes[0])
    assert all(n["label"] for n in nodes)


def test_location_search_resolves_a_named_node_without_any_network_call(monkeypatch):
    client, provider, *_ = _client(monkeypatch)
    name = next(a["name"] for _, a in provider.graph.nodes(data=True) if a.get("name"))
    body = client.get("/locations/search", params={"q": name}).json()
    assert body["geocode"]["source"] == "road_graph_name" and body["snap_distance_m"] < 5 and body["road_label"]["label"]


def test_unknown_place_returns_404_with_attempts_not_a_fabricated_location(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setenv("NOMINATIM_ENABLED", "false")
    import routing.route_optimizer as ro
    ro._GEOCODERS.clear()      # the geocoder is created lazily per graph and reads NOMINATIM_ENABLED at creation
    r = client.get("/locations/search", params={"q": "Definitely Not A Place Qxz"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"


def test_plan_reports_incident_on_route_and_never_claims_incident_free_without_live_data(monkeypatch):
    client, provider, incidents, optimizer, sync = _client(monkeypatch, state="unavailable")
    base = client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8"}).json()["routes"][0]["node_sequence"]
    incidents.add(base[0], base[1], kind="accident", severity=0.6, bidirectional=True, source="simulation")
    r = client.post("/routes/plan", json={"origin_node": "1", "destination_node": "8", "algorithm": "astar"})
    assert r.status_code == 200
    body = r.json()
    assert sync.refreshes == 1
    assert body["decision"]["action"] in {"keep_original", "reroute"} and body["algorithm"] == "astar"
    row = body["incident_analysis"][0]
    assert row["affects_original_route"] is True and row["impact_class"] in {"moderate_accident", "minor_accident", "major_accident"}
    assert body["incident_data"]["incident_free_claim_supported"] is False and "NOT available" in body["incident_data"]["note"]
    assert body["network"]["is_real_osm"] is False and body["data_sources"]["road_network"] == "synthetic_fallback"


def test_plan_rejects_the_same_origin_and_destination(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post("/routes/plan", json={"origin_node": "1", "destination_node": "1"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "same_node"


def test_incident_feed_is_enriched_with_readable_location_and_impact(monkeypatch):
    client, provider, incidents, *_ = _client(monkeypatch)
    nodes = list(provider.graph.nodes)
    u, v = next((u, v) for u, v in provider.graph.edges())
    incidents.add(str(u), str(v), kind="road_closure", severity=1.0, source="manual")
    feed = client.get("/incidents").json()["feed"]
    assert feed[0]["impact_class"] == "confirmed_closure" and feed[0]["routing_effect"] == "block" and feed[0]["location_label"]
    assert nodes


def test_network_roads_endpoint_returns_deduplicated_geometry(monkeypatch):
    client, provider, *_ = _client(monkeypatch)
    body = client.get("/network/roads", params={"level": "all"}).json()
    assert body["is_real_road_geometry"] is False and body["count"] > 0 and body["count"] <= provider.graph.number_of_edges()
