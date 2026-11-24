"""Provider-neutral measured-traffic routing (HERE data through the real matcher + Dijkstra/A*), using a hand-built grid graph.

Proves integration logic and honest labelling only; it is not a live HERE test.
"""
from __future__ import annotations

import networkx as nx
import pytest

from routing.incident_manager import IncidentManager
from routing.osm_network import haversine_m
from routing.route_optimizer import RouteOptimizer
from routing.traffic_routing import corridor_bbox, route_with_provider_flow
from services.traffic_providers import create_provider
from services.traffic_providers.here_provider import HereProvider
from tests.test_here_provider import Clock, FakeClient, Resp

LAT0, LON0, STEP = 20.2961, 85.8245, 0.004
node = lambda r, c: 1000 + r * 10 + c  # noqa: E731
seq = lambda res: [int(n) for n in res["routes"][0]["node_sequence"]]  # the optimizer reports node ids as strings


@pytest.fixture(scope="module")
def graph():
    """2 rows x 4 columns, all two-way, 40 km/h: row 0 is the straight corridor, row 1 a parallel detour."""
    g = nx.MultiDiGraph(source="test_grid")
    for r in range(2):
        for c in range(4):
            g.add_node(node(r, c), x=LON0 + c * STEP, y=LAT0 + r * STEP)
    def link(a, b):
        d = haversine_m(g.nodes[a]["y"], g.nodes[a]["x"], g.nodes[b]["y"], g.nodes[b]["x"])
        for u, v in ((a, b), (b, a)):
            g.add_edge(u, v, 0, length=d, speed_kph=40.0, highway="primary", oneway=False, osmid=u * 100000 + v)
    for r in range(2):
        for c in range(3):
            link(node(r, c), node(r, c + 1))
    for c in range(4):
        link(node(0, c), node(1, c))
    return g


def optimizer(graph):
    return RouteOptimizer(graph, "test_grid", IncidentManager())


def slow_row0_body(speed=1.4, free=11.1, conf=0.9, lat=None):
    lat = LAT0 + 5 / 110_540.0 if lat is None else lat
    pts = [{"lat": lat, "lng": LON0 + c * STEP} for c in range(4)]
    return {"sourceUpdated": "2026-11-14T10:00:00Z", "results": [{
        "location": {"description": "row0", "shape": {"links": [{"points": pts}]}},
        "currentFlow": {"speed": speed, "freeFlow": free, "jamFactor": 9.0, "confidence": conf, "traversability": "open"}}]}


def provider(*responses, key="k", clock=None, **kw):
    return HereProvider(api_key=key, client=FakeClient(*responses), clock=clock or Clock(), **kw)


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_live_here_flow_reroutes_around_slow_corridor(graph, algorithm):
    opt = optimizer(graph)
    base = opt.route_nodes(node(0, 0), node(0, 3), algorithm=algorithm)
    assert set(seq(base)) == {node(0, c) for c in range(4)}  # straight along row 0
    res = route_with_provider_flow(opt, provider(Resp(200, slow_row0_body())), node(0, 0), node(0, 3), algorithm=algorithm)
    info = res["traffic_flow"]
    assert (info["status"], info["traffic_data_state"], info["is_live"], info["provider"]) == ("applied", "live", True, "here")
    assert info["edges_matched"] >= 3 and info["baseline_best_nodes"] == base["routes"][0]["node_sequence"]
    assert set(seq(res)) & {node(1, c) for c in range(4)}  # detoured via row 1
    assert "HERE live flow" in info["note"] and "matched directed edges only" in info["note"]


def test_matching_is_directional_westbound_unaffected(graph):
    opt = optimizer(graph)
    res = route_with_provider_flow(opt, provider(Resp(200, slow_row0_body())), node(0, 3), node(0, 0))  # opposite direction
    assert res["traffic_flow"]["status"] == "applied"
    assert seq(res) == [node(0, 3), node(0, 2), node(0, 1), node(0, 0)]  # eastbound jam does not hit westbound


def test_no_key_leaves_baseline_untouched(graph):
    opt = optimizer(graph)
    plain = opt.route_nodes(node(0, 0), node(0, 3))
    p = provider(Resp(200, slow_row0_body()), key="")
    res = route_with_provider_flow(opt, p, node(0, 0), node(0, 3))
    info = res["traffic_flow"]
    assert (info["status"], info["traffic_data_state"], info["is_live"]) == ("disabled", "disabled", False)
    assert res["routes"][0]["node_sequence"] == plain["routes"][0]["node_sequence"]
    assert res["routes"][0]["estimated_travel_time_min"] == plain["routes"][0]["estimated_travel_time_min"]
    assert not p.client.calls


@pytest.mark.parametrize("responses,reason", [((Resp(500, {}),), "error"), ((Resp(401, {}),), "unauthorized"),
                                              ((Resp(429, {}),), "rate_limited"), ((Resp(200, {"results": []}),), "no_data")])
def test_provider_failure_falls_back_to_osm_routing(graph, responses, reason):
    opt = optimizer(graph)
    plain = opt.route_nodes(node(0, 0), node(0, 3))
    res = route_with_provider_flow(opt, provider(*responses), node(0, 0), node(0, 3))
    info = res["traffic_flow"]
    assert info["status"] == "unavailable" and info["is_live"] is False and info["provider_status"] == reason
    assert res["routes"][0]["node_sequence"] == plain["routes"][0]["node_sequence"]  # OSM Dijkstra result preserved


def test_cached_data_applied_but_not_called_live(graph):
    clock = Clock(); opt = optimizer(graph)
    p = provider(Resp(200, slow_row0_body()), clock=clock)
    p.fetch_flow(*corridor_bbox(opt.route_nodes(node(0, 0), node(0, 3))["routes"]))   # warm the cache
    clock.t += 30
    info = route_with_provider_flow(opt, p, node(0, 0), node(0, 3))["traffic_flow"]
    assert (info["status"], info["traffic_data_state"], info["is_live"]) == ("applied", "cached", False)
    assert info["age_seconds"] == pytest.approx(30) and "cached (30s old)" in info["note"]


def test_stale_cache_applied_but_flagged_not_live(graph):
    clock = Clock(); opt = optimizer(graph)
    p = provider(Resp(200, slow_row0_body()), Resp(500, {}), clock=clock, flow_ttl=60, stale_max=600)
    p.fetch_flow(*corridor_bbox(opt.route_nodes(node(0, 0), node(0, 3))["routes"]))
    clock.t += 120
    info = route_with_provider_flow(opt, p, node(0, 0), node(0, 3))["traffic_flow"]
    assert (info["status"], info["traffic_data_state"], info["is_live"]) == ("applied", "stale_cache", False) and "STALE" in info["note"]


def test_measurements_far_from_roads_do_not_match(graph):
    res = route_with_provider_flow(optimizer(graph), provider(Resp(200, slow_row0_body(lat=LAT0 + 0.05))), node(0, 0), node(0, 3))
    assert res["traffic_flow"]["status"] == "no_edge_match" and res["traffic_flow"]["is_live"] is False


def test_none_provider_and_tomtom_without_key_are_disabled(graph):
    for name in ("none", "tomtom"):
        res = route_with_provider_flow(optimizer(graph), create_provider(name), node(0, 0), node(0, 3))
        assert res["traffic_flow"]["status"] == "disabled" and res["traffic_flow"]["is_live"] is False


def test_corridor_bbox_margin():
    b = corridor_bbox([{"polyline_latlon": [[20.0, 85.0], [20.1, 85.2]]}], margin_deg=0.01)
    assert b == pytest.approx((84.99, 19.99, 85.21, 20.11)) and corridor_bbox([{}]) is None
