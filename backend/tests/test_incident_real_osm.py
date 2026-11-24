"""Incident matching and rerouting on the REAL bundled OpenStreetMap graph of Bhubaneswar.

The TomTom responses are still mocked (no internet, no key): the incident geometries are derived from real OSM edge
shapes, which is how a correct provider geometry would look. This checks the matcher on real road geometry
(dual carriageways, service roads, junctions), not just on the tiny synthetic test grid. Skipped if the cached
graph is not present (backend/cache/bhubaneswar_drive.graphml).
"""
import random
from pathlib import Path

import pytest

from routing.incident_manager import IncidentManager
from routing.incident_matching import match_incident
from routing.osm_network import OSMNetworkProvider
from routing.route_optimizer import RouteOptimizer
from routing.tomtom_routing import edge_index_for
from tests.incident_helpers import FakeTomTom, incident, make_sync, payload

CACHE = Path(__file__).resolve().parents[1] / "cache" / "bhubaneswar_drive.graphml"
pytestmark = pytest.mark.skipif(not CACHE.exists(), reason="cached OSM graph not available")


@pytest.fixture(scope="module")
def real():
    provider = OSMNetworkProvider()
    graph = provider.load(allow_fallback=False)
    assert provider.is_real_osm
    return graph, edge_index_for(graph)


def coords(graph, u, v, data):
    geom = data.get("geometry")
    return [(x, y) for x, y in geom.coords] if geom is not None else [(graph.nodes[u]["x"], graph.nodes[u]["y"]), (graph.nodes[v]["x"], graph.nodes[v]["y"])]


def sample_edges(graph, n, seed):
    edges = [(u, v, k, d) for u, v, k, d in graph.edges(keys=True, data=True) if d.get("length", 0) >= 60]
    return random.Random(seed).sample(edges, n)


def test_real_geometry_matches_the_exact_directed_edge_and_never_a_wrong_one(real):
    graph, idx = real
    correct = wrong = declined = 0
    for u, v, k, d in sample_edges(graph, 200, seed=7):
        shifted = [[x, y + 3 / 110_540] for x, y in coords(graph, u, v, d)]     # ~3 m provider/GPS offset
        res = match_incident(graph, idx, {"type": "LineString", "coordinates": shifted})
        if not res.matched:
            declined += 1
            assert res.reason in {"ambiguous_parallel_roads", "low_confidence", "no_nearby_road", "direction_mismatch"}
        elif (str(u), str(v), str(k)) in {(e["u"], e["v"], e["key"]) for e in res.edges}:
            correct += 1
        else:
            wrong += 1
    assert wrong == 0, "the matcher must decline rather than apply an incident to the wrong road"
    assert correct / 200 >= 0.80, f"only {correct}/200 matched exactly (declined {declined})"


def test_reversed_geometry_never_lands_on_the_original_direction(real):
    graph, idx = real
    for u, v, k, d in sample_edges(graph, 150, seed=11):
        rev = [[x, y + 3 / 110_540] for x, y in coords(graph, u, v, d)][::-1]
        res = match_incident(graph, idx, {"type": "LineString", "coordinates": rev})
        assert all((e["u"], e["v"]) != (str(u), str(v)) for e in res.edges)


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_closure_on_a_real_route_triggers_a_real_reroute(real, algorithm):
    graph, _ = real
    probe = RouteOptimizer(graph, "openstreetmap_cached", IncidentManager())
    start, end = probe.nearest_node(20.2706, 85.8430), probe.nearest_node(20.3500, 85.8200)   # Master Canteen -> Patia
    base = probe.route_nodes(start, end, algorithm=algorithm, alternatives=1)["routes"][0]
    seq = [int(n) for n in base["node_sequence"]]
    third = len(seq) // 3
    chosen = None
    for i in range(third, 2 * third):                                             # try edges in the middle of the route
        u, v = seq[i], seq[i + 1]
        data = min(graph[u][v].values(), key=lambda e: e.get("length", 1e9))
        if data.get("length", 0) < 60:
            continue
        key = next(k for k, e in graph[u][v].items() if e is data)
        line = [[x, y + 3 / 110_540] for x, y in coords(graph, u, v, data)]
        sync, _, mgr, _, _ = make_sync(FakeTomTom(payload(incident("REAL", icon=8, coords=line, description="Closed"))), graph=graph)
        sync.refresh()
        feed = mgr.feed()[0]
        if feed["status"] == "applied" and (str(u), str(v)) in {(e["u"], e["v"]) for e in feed["edges"]}:
            chosen = (u, v, mgr)
            break
    assert chosen, "no edge on the route could be matched confidently"
    u, v, mgr = chosen
    opt = RouteOptimizer(graph, "openstreetmap_cached", mgr)
    new = opt.route_nodes(start, end, algorithm=algorithm, alternatives=1)["routes"][0]
    new_seq = [int(n) for n in new["node_sequence"]]
    assert new_seq != seq
    assert (u, v) not in set(zip(new_seq, new_seq[1:])), "the closed directed edge must not be used"
    assert new["estimated_travel_time_min"] >= base["estimated_travel_time_min"]
