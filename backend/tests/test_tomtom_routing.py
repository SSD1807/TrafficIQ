"""TomTom flow -> directed OSM edges -> routing cost.

The HTTP layer is mocked with payloads in TomTom's documented flowSegmentData shape, so the real
TomTomService, payload parser, spatial matcher and routing algorithm all run. This proves the
integration logic; it does NOT prove behaviour against the live TomTom service (no key/network here).
"""
from __future__ import annotations

import math
from pathlib import Path

import httpx
import pytest

from routing.flow_matching import EdgeIndex, build_measured_edge_table, match_measurement, parse_flow_payload
from routing.route_optimizer import RouteOptimizer
from routing.tomtom_routing import route_with_tomtom_flow, sample_polyline
from services.tomtom_service import TomTomService

FIXTURE = Path(__file__).parent / "fixtures" / "mini_grid.osm"
LAT0, LON0, STEP = 20.2961, 85.8245, 0.004
node = lambda r, c: 1000 + r * 10 + c  # noqa: E731  (ids used by the fixture)


@pytest.fixture(scope="module")
def graph():
    """Real OSMnx parse of the fixture, unsimplified so every grid corner stays a node (clean 4x4 topology)."""
    import osmnx as ox
    G = ox.graph_from_xml(str(FIXTURE), simplify=False, retain_all=False)
    G = ox.add_edge_travel_times(ox.add_edge_speeds(G))
    return G, "openstreetmap_file"


def row0_segment(current=5.0, free=50.0, confidence=0.95, closure=False, eastbound=True, offset_m=5.0):
    lons = [LON0 + c * STEP for c in range(4)]
    lat = LAT0 + offset_m / 110_540.0  # TomTom shifts geometry toward the side of travel
    pts = [{"latitude": lat, "longitude": lon} for lon in (lons if eastbound else lons[::-1])]
    return {"flowSegmentData": {"frc": "FRC2", "currentSpeed": current, "freeFlowSpeed": free, "currentTravelTime": 99,
                                "freeFlowTravelTime": 20, "confidence": confidence, "roadClosure": closure,
                                "coordinates": {"coordinate": pts}}}


def make_service(segment=None, *, status=200, key="test-key"):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("point")))
        lat, lon = map(float, request.url.params["point"].split(","))
        if status != 200 or segment is None or abs(lat - LAT0) > 0.0012:  # only row 0 has a measured road nearby
            return httpx.Response(status if status != 200 else 400, json={"error": "Point too far from nearest existing road"})
        return httpx.Response(200, json=segment)

    svc = TomTomService(api_key=key, client=httpx.Client(transport=httpx.MockTransport(handler)))
    svc.calls = calls
    return svc


def test_parse_requires_live_ok_payload():
    svc = make_service(row0_segment())
    payload = svc.flow_at(LAT0, LON0 + STEP)
    m = parse_flow_payload(payload, LAT0, LON0 + STEP)
    assert m and m.current_speed_kph == 5 and m.free_flow_speed_kph == 50 and m.factor == 6.0  # 10x capped at 6x
    assert parse_flow_payload({"status": "disabled", "is_live": False}, 0, 0) is None
    assert parse_flow_payload({"status": "error", "is_live": False}, 0, 0) is None
    assert parse_flow_payload({"status": "ok", "is_live": True, "data": {}}, 0, 0) is None


def test_measurement_matches_only_the_directed_edges_in_its_direction(graph):
    g, _ = graph
    idx = EdgeIndex.build(g)
    east = parse_flow_payload(make_service(row0_segment(eastbound=True)).flow_at(LAT0, LON0), LAT0, LON0)
    hits = {(u, v) for u, v, _k, _o in match_measurement(idx, east)}
    assert hits == {(node(0, 0), node(0, 1)), (node(0, 1), node(0, 2)), (node(0, 2), node(0, 3))}
    west = parse_flow_payload(make_service(row0_segment(eastbound=False)).flow_at(LAT0, LON0), LAT0, LON0)
    hits_w = {(u, v) for u, v, _k, _o in match_measurement(idx, west)}
    assert hits_w == {(node(0, 3), node(0, 2)), (node(0, 2), node(0, 1)), (node(0, 1), node(0, 0))}
    assert not hits & hits_w  # opposite directions never share an edge
    # neighbouring parallel street (row 1, ~445 m away) is never matched
    row1 = {node(1, c) for c in range(4)}
    assert not any(u in row1 or v in row1 for u, v in hits | hits_w)


def test_low_confidence_measurements_are_ignored(graph):
    g, _ = graph
    m = parse_flow_payload(make_service(row0_segment(confidence=0.2)).flow_at(LAT0, LON0), LAT0, LON0)
    assert build_measured_edge_table(EdgeIndex.build(g), [m], min_confidence=0.5) == {}


def test_measured_congestion_changes_the_route_and_only_in_the_measured_direction(graph):
    g, src = graph
    opt = RouteOptimizer(g, src)
    o, d = node(0, 0), node(0, 3)
    base = opt.route_nodes(o, d, alternatives=1)["routes"][0]
    assert all(int(n) // 10 == 100 for n in base["node_sequence"])  # baseline drives straight along row 0
    res = route_with_tomtom_flow(opt, make_service(row0_segment(current=5, free=50)), o, d, alternatives=2)
    tf = res["traffic_flow"]
    assert tf["status"] == "applied" and tf["is_live"] is True and tf["edges_matched"] == 3 and tf["probes_ok"] >= 1 and 1 <= tf["measurements_matched"] <= tf["probes_ok"]
    best = res["routes"][0]
    assert best["node_sequence"] != base["node_sequence"]  # algorithm re-routed around measured congestion
    assert best["measured_distance_share"] == 0.0           # and the chosen detour carries no measured edges
    # the congested direct route is still offered, priced with the measured speeds
    direct = [r for r in res["routes"] if r["node_sequence"] == base["node_sequence"]]
    if direct:
        assert direct[0]["estimated_travel_time_min"] > base["estimated_travel_time_min"] * 3
        assert direct[0]["measured_distance_share"] == 1.0
    # westbound traffic (opposite direction, unmeasured) is untouched
    rev_base = opt.route_nodes(d, o, alternatives=1)["routes"][0]
    rev = route_with_tomtom_flow(opt, make_service(row0_segment(current=5, free=50)), d, o, alternatives=1)["routes"][0]
    assert rev["node_sequence"] == rev_base["node_sequence"] and rev["estimated_travel_time_min"] == rev_base["estimated_travel_time_min"]


def test_free_flowing_measurements_do_not_speed_roads_up(graph):
    g, src = graph
    opt = RouteOptimizer(g, src)
    o, d = node(0, 0), node(0, 3)
    base = opt.route_nodes(o, d, alternatives=1)["routes"][0]
    res = route_with_tomtom_flow(opt, make_service(row0_segment(current=70, free=50)), o, d, alternatives=1)
    assert res["traffic_flow"]["status"] == "applied"
    assert res["routes"][0]["estimated_travel_time_min"] == base["estimated_travel_time_min"]  # factor floors at 1.0


def test_measured_road_closure_removes_the_edge(graph):
    g, src = graph
    opt = RouteOptimizer(g, src)
    res = route_with_tomtom_flow(opt, make_service(row0_segment(closure=True)), node(0, 0), node(0, 3), alternatives=3)
    assert res["traffic_flow"]["status"] == "applied"
    for r in res["routes"]:
        for a, b in zip(r["node_sequence"][:-1], r["node_sequence"][1:]):
            assert (int(a), int(b)) not in {(node(0, 0), node(0, 1)), (node(0, 1), node(0, 2)), (node(0, 2), node(0, 3))}


def test_no_api_key_means_disabled_and_identical_to_baseline(graph):
    g, src = graph
    opt = RouteOptimizer(g, src)
    base = opt.route_nodes(node(0, 0), node(0, 3), alternatives=2)
    res = route_with_tomtom_flow(opt, _no_key(), node(0, 0), node(0, 3), alternatives=2)
    assert res["traffic_flow"]["status"] == "disabled" and res["traffic_flow"]["is_live"] is False
    assert [r["node_sequence"] for r in res["routes"]] == [r["node_sequence"] for r in base["routes"]]


def _no_key():
    svc = TomTomService(api_key=None, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    svc.api_key = None  # env may define one on the developer's machine
    return svc


def test_upstream_errors_fall_back_to_baseline_and_never_claim_live(graph):
    g, src = graph
    opt = RouteOptimizer(g, src)
    base = opt.route_nodes(node(0, 0), node(0, 3), alternatives=1)
    res = route_with_tomtom_flow(opt, make_service(row0_segment(), status=503), node(0, 0), node(0, 3), alternatives=1)
    tf = res["traffic_flow"]
    assert tf["status"] == "unavailable" and tf["is_live"] is False and tf["probes_ok"] == 0
    assert res["routes"][0]["node_sequence"] == base["routes"][0]["node_sequence"]
    assert res["routes"][0]["measured_segment_count"] == 0


def test_probe_budget_is_respected(graph):
    g, src = graph
    svc = make_service(row0_segment())
    res = route_with_tomtom_flow(RouteOptimizer(g, src), svc, node(0, 0), node(0, 3), alternatives=3, max_calls=3, spacing_m=100)
    assert res["traffic_flow"]["probes_requested"] <= 3 and len(svc.calls) <= 3
    assert res["traffic_flow"]["probes_skipped_over_budget"] > 0


def test_sample_polyline_spacing():
    pts = [[20.0, 85.0], [20.0, 85.0 + 0.01]]  # ~1045 m east-west
    s = sample_polyline(pts, 400)
    assert s[0] == (20.0, 85.0) and s[-1] == (20.0, 85.01) and 3 <= len(s) <= 5


def test_api_route_endpoint_reports_traffic_flow_status():
    from fastapi.testclient import TestClient
    from backend.dependencies import get_provider
    from backend.main import app

    provider = get_provider()
    graph = provider.load(allow_fallback=True)

    origin, destination = next(iter(graph.edges()))

    response = TestClient(app).post(
        "/routes/calculate",
        json={
            "origin_node": str(origin),
            "destination_node": str(destination),
            "use_tomtom_flow": True,
        },
    )

    assert response.status_code == 200

    data = response.json()
    assert "traffic_flow" in data
    assert data["traffic_flow"]["status"] in {
        "disabled",
        "unavailable",
    }
    assert data["traffic_flow"]["is_live"] is False