"""Incident-aware routing engine: closures, accidents, construction-like penalties, unrelated and unmatched incidents.

Two layers:
* a small hermetic grid (exact, hand-checkable numbers), and
* the REAL cached Bhubaneswar OSM graph. TomTom payloads are mocked (no network/key) but their geometries are taken from
  real OSM edge shapes, exactly what a correct provider geometry looks like. These incidents are TEST DATA only.
"""
from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path

import pytest

from routing.engine import IncidentRoutingEngine
from routing.impact_rules import ImpactRules, set_rules
from routing.incident_manager import IncidentManager
from routing.incident_matching import match_incident
from routing.osm_network import OSMNetworkProvider
from routing.tomtom_routing import edge_index_for
from services.incident_sync import IncidentSyncService
from services.tomtom_incidents import normalize_incident
from tests.incident_helpers import corridor_graph, incident, node

ALGOS = ["dijkstra", "astar"]
CACHE = Path(__file__).resolve().parents[1] / "cache" / "bhubaneswar_drive.graphml"


# --------------------------------------------------------------------------- hermetic grid
def grid_engine(manager=None):
    g = corridor_graph()
    return IncidentRoutingEngine(g, "test_grid", manager or IncidentManager()), g


def add(manager, u, v, kind, severity, **kw):
    return manager.add(u, v, kind=kind, severity=severity, bidirectional=True, source="simulation", **kw)


@pytest.mark.parametrize("algo", ALGOS)
def test_grid_no_incident_keeps_original(algo):
    eng, _ = grid_engine()
    out = eng.plan(node(0, 0), node(0, 3), algorithm=algo)
    assert out["decision"]["action"] == "keep_original" and out["comparison"]["incidents_avoided"] == []
    assert out["original_route"]["node_sequence"] == out["recommended_route"]["node_sequence"]


@pytest.mark.parametrize("algo", ALGOS)
def test_grid_closure_blocks_edge_and_reroutes(algo):
    m = IncidentManager()
    add(m, node(0, 1), node(0, 2), "road_closure", 1.0)
    eng, _ = grid_engine(m)
    out = eng.plan(node(0, 0), node(0, 3), algorithm=algo)
    rec = [str(n) for n in out["recommended_route"]["node_sequence"]]
    assert out["decision"]["action"] == "reroute" and out["original_route"]["blocked_by_closure"] is True
    assert (str(node(0, 1)), str(node(0, 2))) not in zip(rec[:-1], rec[1:]) and str(node(1, 1)) in rec
    assert out["comparison"]["distance_delta_km"] > 0 and len(out["comparison"]["incidents_avoided"]) == 1
    row = out["incident_analysis"][0]
    assert row["impact_class"] == "confirmed_closure" and row["routing_effect"] == "block"
    assert row["affects_original_route"] and not row["affects_recommended_route"]


@pytest.mark.parametrize("algo", ALGOS)
def test_grid_major_accident_stays_passable_but_reroutes_when_delay_is_large(algo):
    m = IncidentManager()
    add(m, node(0, 1), node(0, 2), "accident", 0.9)
    eng, _ = grid_engine(m)
    out = eng.plan(node(0, 0), node(0, 3), algorithm=algo)
    assert out["original_route"]["blocked_by_closure"] is False            # severity alone never closes a road
    assert out["original_route"]["time_with_incidents_min"] > out["original_route"]["free_flow_time_min"]
    assert out["decision"]["action"] == "reroute"
    assert out["incident_analysis"][0]["impact_class"] == "major_accident"


@pytest.mark.parametrize("algo", ALGOS)
def test_grid_minor_accident_is_kept_with_warning_not_rerouted(algo):
    m = IncidentManager()
    add(m, node(0, 1), node(0, 2), "accident", 0.3)
    eng, _ = grid_engine(m)
    out = eng.plan(node(0, 0), node(0, 3), algorithm=algo)
    assert out["decision"]["action"] == "keep_original" and out["decision"]["triggering_incidents"]
    assert out["recommended_route"]["incident_count"] == 1                  # warning is still reported
    assert out["incident_analysis"][0]["impact_class"] == "minor_accident"


@pytest.mark.parametrize("algo", ALGOS)
def test_grid_unrelated_incident_does_not_change_route(algo):
    m = IncidentManager()
    add(m, node(1, 1), node(1, 2), "road_closure", 1.0)                     # on the detour road, not on the route
    eng, _ = grid_engine(m)
    out = eng.plan(node(0, 0), node(0, 3), algorithm=algo)
    assert out["decision"]["action"] == "keep_original" and out["original_route"]["incident_count"] == 0
    assert out["incident_analysis"][0]["affects_original_route"] is False


def test_grid_exclude_sources_ignores_those_incidents():
    m = IncidentManager()
    add(m, node(0, 1), node(0, 2), "road_closure", 1.0)
    eng, _ = grid_engine(m)
    out = eng.plan(node(0, 0), node(0, 3), exclude_sources={"simulation"})
    assert out["decision"]["action"] == "keep_original" and out["incident_analysis"] == []


def test_grid_closure_with_no_alternative_reports_no_route():
    import networkx as nx
    g = nx.MultiDiGraph(source="test_grid")
    for i in range(3):
        g.add_node(i, x=85.8 + i * 0.004, y=20.3)
    for a, b in ((0, 1), (1, 2)):
        for u, v in ((a, b), (b, a)):
            g.add_edge(u, v, 0, length=400.0, speed_kph=40.0, highway="primary", name="Only Road")
    m = IncidentManager()
    add(m, 1, 2, "road_closure", 1.0)
    out = IncidentRoutingEngine(g, "test_grid", m).plan(0, 2)
    assert out["decision"]["action"] == "no_route" and out["recommended_route"] is None
    assert "closure" in out["decision"]["reason"].lower()


def test_provider_delay_replaces_rule_delay_and_is_not_double_counted():
    rules = ImpactRules()
    rule = rules.edge_effect({"kind": "accident", "severity": 0.9})
    prov = rules.edge_effect({"kind": "accident", "severity": 0.9, "edge_delay_s": 240.0})
    assert rule["delay_basis"] == "rule_default" and rule["cost_factor"] > 1.0
    assert prov == {"effect": "penalty", "cost_factor": 1.0, "delay_s": 240.0, "delay_basis": "provider"}
    assert rules.edge_effect({"kind": "road_closure", "severity": 1.0})["effect"] == "block"
    assert rules.edge_effect({"kind": "road_closure", "severity": 1.0, "edge_delay_s": 5})["effect"] == "block"


def test_rules_are_configurable_without_code_changes():
    custom = ImpactRules.from_dict({"reroute_min_saving_s": 10, "kinds": {"accident": {"delay_base_s": 0, "delay_per_severity_s": 0, "factor_per_severity": 0}}})
    assert custom.reroute_min_saving_s == 10 and custom.edge_effect({"kind": "accident", "severity": 1.0})["delay_s"] == 0
    try:
        ImpactRules.from_dict({"nonsense": 1})
    except ValueError:
        pass
    else:
        raise AssertionError("unknown rule names must be rejected")
    m = IncidentManager()
    add(m, node(0, 1), node(0, 2), "accident", 0.3)
    set_rules(custom)
    try:
        eng, _ = grid_engine(m)
        eng.rules = custom
        assert eng.plan(node(0, 0), node(0, 3))["decision"]["action"] == "keep_original"
    finally:
        set_rules(None)


def test_unconfirmed_provider_closure_is_shown_but_never_blocks():
    rules = ImpactRules()
    row = rules.explain({"kind": "road_closure", "severity": 1.0, "confirmed": False})
    assert row["impact_class"] == "unconfirmed_closure" and row["routing_effect"] == "none"


# --------------------------------------------------------------------------- real cached OSM graph
requires_cache = pytest.mark.skipif(not CACHE.exists(), reason="cached OSM graph not available")


@lru_cache(maxsize=1)
def real():
    provider = OSMNetworkProvider(CACHE)
    graph = provider.require_graph()
    assert provider.is_real_osm
    return graph, edge_index_for(graph), provider.source


def real_pair():
    graph, _, _ = real()
    nodes = list(graph.nodes)
    return nodes[100], nodes[9000]


def edge_coords(graph, u, v, d):
    geom = d.get("geometry")
    return [(x, y) for x, y in geom.coords] if geom is not None else [(graph.nodes[u]["x"], graph.nodes[u]["y"]), (graph.nodes[v]["x"], graph.nodes[v]["y"])]


def provider_incident_on_route(graph, idx, path, pid, **kw):
    """First mid-route edge whose provider-style geometry the matcher accepts on exactly that directed edge."""
    seq = [str(n) for n in path]
    on_route = set(zip(seq[:-1], seq[1:]))
    for u, v, k, d in graph.edges(keys=True, data=True):
        if (str(u), str(v)) not in on_route or d.get("length", 0) < 90:
            continue
        shifted = [[x, y + 3 / 110_540] for x, y in edge_coords(graph, u, v, d)]
        raw = incident(pid, coords=shifted, **kw)
        inc, _ = normalize_incident(raw)
        res = match_incident(graph, idx, inc["geometry"], road_numbers=inc["road_numbers"])
        if res.matched and (str(u), str(v), str(k)) in {(e["u"], e["v"], e["key"]) for e in res.edges}:
            return raw, (str(u), str(v))
    raise AssertionError("no matchable mid-route edge found")


def load_provider_incidents(graph, idx, manager, raws):
    entries = []
    for raw in raws:
        inc, err = normalize_incident(raw)
        assert inc is not None, err
        res = match_incident(graph, idx, inc["geometry"], road_numbers=inc["road_numbers"])
        applied, skip = IncidentSyncService._routing_decision(inc, res)
        entries.append({"incident": inc, "match": res.to_public(), "routing_applied": applied, "routing_skip_reason": skip})
    manager.sync_provider_incidents(entries)


def real_engine(manager):
    graph, _, source = real()
    return IncidentRoutingEngine(graph, source, manager)


def normal_path(algo):
    s, e = real_pair()
    out = real_engine(IncidentManager()).plan(s, e, algorithm=algo)
    assert out["decision"]["action"] == "keep_original" and out["original_route"]["is_real_road_geometry"]
    return out["original_route"]["node_sequence"]


@pytest.mark.parametrize("algo", ALGOS)
@requires_cache
def test_real_normal_route_uses_the_osm_graph(algo):
    path = normal_path(algo)
    assert len(path) > 10


@pytest.mark.parametrize("algo", ALGOS)
@requires_cache
def test_real_confirmed_tomtom_closure_reroutes_and_avoids_closed_edge(algo):
    graph, idx, _ = real()
    path = normal_path(algo)
    raw, blocked_edge = provider_incident_on_route(graph, idx, path, "closure-1", icon=8, magnitude=4)
    m = IncidentManager()
    load_provider_incidents(graph, idx, m, [raw])
    s, e = real_pair()
    out = real_engine(m).plan(s, e, algorithm=algo)
    rec = [str(n) for n in out["recommended_route"]["node_sequence"]]
    assert out["decision"]["action"] == "reroute" and out["original_route"]["blocked_by_closure"]
    assert blocked_edge not in set(zip(rec[:-1], rec[1:]))
    row = next(r for r in out["incident_analysis"] if r["id"] == "tomtom:closure-1")
    assert row["impact_class"] == "confirmed_closure" and row["matched"] and row["routing_applied"]
    assert row["affects_original_route"] and not row["affects_recommended_route"]
    assert out["comparison"]["incidents_avoided"] == ["tomtom:closure-1"]
    # The normal route is the TIME-optimal one, so a detour can never be faster than it; its distance may differ either way.
    assert out["comparison"]["time_delta_vs_original_free_flow_min"] >= 0


@pytest.mark.parametrize("algo", ALGOS)
@requires_cache
def test_real_major_accident_is_evaluated_and_not_treated_as_closure(algo):
    graph, idx, _ = real()
    path = normal_path(algo)
    raw, edge = provider_incident_on_route(graph, idx, path, "acc-major", icon=1, magnitude=3, description="Accident", delay=1800)
    m = IncidentManager()
    load_provider_incidents(graph, idx, m, [raw])
    s, e = real_pair()
    out = real_engine(m).plan(s, e, algorithm=algo)
    row = next(r for r in out["incident_analysis"] if r["id"] == "tomtom:acc-major")
    assert row["impact_class"] == "major_accident" and row["routing_effect"] == "penalty" and row["provider_delay_s"] == 1800
    assert out["original_route"]["blocked_by_closure"] is False
    assert out["original_route"]["time_with_incidents_min"] > out["original_route"]["free_flow_time_min"] + 20
    assert out["decision"]["action"] == "reroute"           # a 30-minute provider delay justifies a detour in a city grid
    assert edge not in set(zip(*(lambda r: (r[:-1], r[1:]))([str(n) for n in out["recommended_route"]["node_sequence"]])))


@pytest.mark.parametrize("algo", ALGOS)
@requires_cache
def test_real_minor_accident_does_not_force_a_reroute(algo):
    graph, idx, _ = real()
    path = normal_path(algo)
    raw, _ = provider_incident_on_route(graph, idx, path, "acc-minor", icon=1, magnitude=1, description="Accident", delay=20)
    m = IncidentManager()
    load_provider_incidents(graph, idx, m, [raw])
    s, e = real_pair()
    out = real_engine(m).plan(s, e, algorithm=algo)
    assert out["decision"]["action"] == "keep_original"
    assert out["original_route"]["node_sequence"] == out["recommended_route"]["node_sequence"]
    assert out["recommended_route"]["incident_count"] == 1


@pytest.mark.parametrize("algo", ALGOS)
@requires_cache
def test_real_unrelated_incident_does_not_change_the_route(algo):
    graph, idx, _ = real()
    path = normal_path(algo)
    on = set(zip([str(n) for n in path][:-1], [str(n) for n in path][1:]))
    rng = random.Random(3)
    edges = [(u, v, k, d) for u, v, k, d in graph.edges(keys=True, data=True) if d.get("length", 0) >= 90 and (str(u), str(v)) not in on and (str(v), str(u)) not in on]
    far = None
    path_nodes = [graph.nodes[n] for n in ({str(k): k for k in graph.nodes}[str(p)] for p in path)]
    for u, v, k, d in rng.sample(edges, 400):
        a = graph.nodes[u]
        if all(abs(float(a["y"]) - float(n["y"])) > 0.01 or abs(float(a["x"]) - float(n["x"])) > 0.01 for n in path_nodes):
            far = (u, v, k, d)
            break
    assert far, "no edge far from the route found"
    u, v, k, d = far
    raw = incident("far-closure", icon=8, magnitude=4, coords=[[x, y + 3 / 110_540] for x, y in edge_coords(graph, u, v, d)])
    m = IncidentManager()
    load_provider_incidents(graph, idx, m, [raw])
    s, e = real_pair()
    out = real_engine(m).plan(s, e, algorithm=algo)
    assert out["decision"]["action"] == "keep_original" and out["original_route"]["incident_count"] == 0
    assert out["original_route"]["node_sequence"] == path


@requires_cache
def test_real_incident_far_from_any_road_is_shown_unmatched_and_never_applied():
    graph, idx, _ = real()
    rng = random.Random(11)
    ys = [float(d["y"]) for _, d in graph.nodes(data=True)]
    xs = [float(d["x"]) for _, d in graph.nodes(data=True)]
    lat0, lon0 = sum(ys) / len(ys), sum(xs) / len(xs)
    spot = None
    for _ in range(3000):
        lat, lon = lat0 + rng.uniform(-0.05, 0.05), lon0 + rng.uniform(-0.05, 0.05)
        raw = incident("nowhere", icon=8, magnitude=4, coords=[[lon, lat], [lon + 0.0003, lat]])
        inc, _ = normalize_incident(raw)
        res = match_incident(graph, idx, inc["geometry"])
        if not res.matched and res.reason == "no_nearby_road":
            spot = raw
            break
    assert spot, "could not find an off-road spot inside the graph"
    m = IncidentManager()
    load_provider_incidents(graph, idx, m, [spot])
    s, e = real_pair()
    out = real_engine(m).plan(s, e)
    row = out["incident_analysis"][0]
    assert row["matched"] is False and row["routing_applied"] is False and row["unmatched_reason"]
    assert row["routing_effect"] == "none" and row["affects_original_route"] is False
    assert out["decision"]["action"] == "keep_original"
