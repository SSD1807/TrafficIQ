"""Incident-aware routing: closures, severity, Dijkstra + A*, no double counting, manual incidents intact."""
import networkx as nx
import pytest

from routing.incident_impact import compute_incident_impact
from routing.incident_manager import IncidentManager
from routing.route_optimizer import RouteOptimizer
from tests.incident_helpers import (FakeTomTom, corridor_graph, incident, make_sync, node, optimizer_for, payload, row0_line)

A, B = node(0, 0), node(0, 3)
seq = lambda res: [int(n) for n in res["routes"][0]["node_sequence"]]  # noqa: E731
STRAIGHT = [node(0, c) for c in range(4)]


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_confirmed_tomtom_closure_reroutes_via_dijkstra_and_astar(algorithm):
    sync, _, mgr, graph, _ = make_sync(FakeTomTom(payload(incident("C", icon=8))))
    opt = optimizer_for(graph, mgr)
    assert seq(opt.route_nodes(A, B, algorithm=algorithm)) == STRAIGHT        # before: straight along the corridor
    sync.refresh()
    after = opt.route_nodes(A, B, algorithm=algorithm, alternatives=3)
    assert seq(after) != STRAIGHT and after["active_incident_count"] == 1
    path = seq(after)
    assert not any((u, v) == (node(0, 1), node(0, 2)) for u, v in zip(path, path[1:]))   # the closed directed edge is not used
    assert after["routes"][0]["estimated_travel_time_min"] > 0


def test_closure_is_directional_the_opposite_carriageway_stays_usable():
    sync, _, mgr, graph, _ = make_sync(FakeTomTom(payload(incident("C", icon=8, coords=row0_line(1.0, 2.0)))))
    sync.refresh()
    opt = optimizer_for(graph, mgr)
    assert seq(opt.route_nodes(A, B)) != STRAIGHT                              # eastbound blocked
    assert seq(opt.route_nodes(B, A)) == STRAIGHT[::-1]                        # westbound unaffected


def test_dijkstra_and_astar_agree_with_live_incidents_applied():
    sync, _, mgr, graph, _ = make_sync(FakeTomTom(payload(incident("X", icon=1, magnitude=3))))
    sync.refresh()
    opt = optimizer_for(graph, mgr)
    d, a = opt.route_nodes(A, B, algorithm="dijkstra"), opt.route_nodes(A, B, algorithm="astar")
    assert abs(d["routes"][0]["estimated_travel_time_min"] - a["routes"][0]["estimated_travel_time_min"]) < 1e-8


def test_higher_provider_severity_costs_more_and_can_trigger_a_reroute():
    def best(magnitude):
        sync, _, mgr, graph, _ = make_sync(FakeTomTom(payload(incident("S", icon=1, magnitude=magnitude))))
        sync.refresh()
        return optimizer_for(graph, mgr).route_nodes(A, B)["routes"][0]
    base = optimizer_for(corridor_graph(), IncidentManager()).route_nodes(A, B)["routes"][0]
    minor, major = best(1), best(3)
    assert major["estimated_travel_time_min"] >= minor["estimated_travel_time_min"] >= base["estimated_travel_time_min"]
    assert major["node_sequence"] != base["node_sequence"]                    # a major accident is worth detouring around


def test_incident_effect_is_applied_exactly_once_however_often_it_is_polled():
    fake = FakeTomTom(payload(incident("S", icon=1, magnitude=2)))
    sync, _, mgr, graph, clock = make_sync(fake)
    opt = optimizer_for(graph, mgr)
    sync.refresh()
    once = opt.route_nodes(A, node(0, 2), alternatives=1)["routes"][0]["estimated_travel_time_min"]
    for _ in range(4):
        clock.advance(61)
        sync.refresh()
    assert opt.route_nodes(A, node(0, 2), alternatives=1)["routes"][0]["estimated_travel_time_min"] == once
    assert len([i for i in mgr.list_active() if i["source"] == "tomtom"]) == 1


def test_one_incident_id_is_never_priced_twice_on_the_same_edge():
    g, mgr = corridor_graph(), IncidentManager()
    mgr.add(node(0, 1), node(0, 2), kind="accident", severity=0.5, external_id="same")
    once = optimizer_for(g, mgr)._routing_view(None, 0.0)[node(0, 1)][node(0, 2)]["travel_time_sec"]
    mgr._items["dup"] = {**mgr._items["same"], "id": "same"}                  # same id re-registered via another key
    twice = optimizer_for(g, mgr)._routing_view(None, 0.0)[node(0, 1)][node(0, 2)]["travel_time_sec"]
    assert once == twice


def test_edge_key_targets_only_the_matched_parallel_edge():
    g = corridor_graph()
    u, v = node(0, 1), node(0, 2)
    g.add_edge(u, v, 1, **{**g.edges[u, v, 0], "name": "Frontage lane"})      # a parallel edge between the same nodes
    mgr = IncidentManager()
    mgr.add(u, v, kind="road_closure", severity=1.0, source="tomtom", edge_key="0", external_id="k0")
    view = optimizer_for(g, mgr)._routing_view(None, 0.0)
    assert view.has_edge(u, v) and view[u][v]["name"] == "Frontage lane"       # key 0 closed, key 1 still drivable


def test_traffic_jam_adds_delay_but_is_not_double_counted_where_flow_is_measured():
    g, mgr = corridor_graph(), IncidentManager()
    u, v = node(0, 1), node(0, 2)
    base = optimizer_for(g, mgr)._routing_view(None, 0.0)[u][v]["travel_time_sec"]
    mgr.add(u, v, kind="traffic_jam", severity=0.6, source="tomtom", external_id="jam")
    jammed = optimizer_for(g, mgr)._routing_view(None, 0.0)[u][v]["travel_time_sec"]
    assert jammed > base
    measured = {(u, v): {"factor": 2.0, "current_kph": 25.0}}   # same shape the TomTom flow matcher produces
    with_jam = optimizer_for(g, mgr)._routing_view(None, 0.0, measured_by_edge=measured)[u][v]["travel_time_sec"]
    without = optimizer_for(g, IncidentManager())._routing_view(None, 0.0, measured_by_edge=measured)[u][v]["travel_time_sec"]
    assert with_jam == without and with_jam > base                            # measured flow priced it; the jam is not added again


def test_routing_works_when_tomtom_is_unavailable():
    sync, _, mgr, graph, _ = make_sync(FakeTomTom(__import__("httpx").ConnectError("down")))
    out = sync.refresh()
    assert out["refreshed"] is False and out["provider_status"]["state"] == "unavailable"
    assert seq(optimizer_for(graph, mgr).route_nodes(A, B)) == STRAIGHT        # normal routing, no incidents, no error


def test_closure_that_cuts_the_only_path_is_reported_not_crashed():
    g = corridor_graph()
    g.remove_edges_from([(node(0, 0), node(1, 0)), (node(1, 0), node(0, 0))])  # A can now only leave via the corridor
    mgr = IncidentManager()
    mgr.add(node(0, 0), node(0, 1), kind="road_closure", severity=1.0, source="tomtom", external_id="x")
    with pytest.raises(nx.NetworkXNoPath):
        optimizer_for(g, mgr).route_nodes(node(0, 0), B)
    impact = compute_incident_impact(RouteOptimizer(g, "test_grid", IncidentManager()), node(0, 1), B)   # sanity: no incidents
    assert impact["rerouted"] is False


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_incident_impact_compares_routes_with_and_without_live_incidents(algorithm):
    sync, _, mgr, graph, _ = make_sync(FakeTomTom(payload(incident("C", icon=8))))
    mgr.add(node(1, 2), node(1, 3), kind="hazard", severity=0.2, source="manual", description="manual keeps applying")
    sync.refresh()
    impact = compute_incident_impact(optimizer_for(graph, mgr), A, B, algorithm=algorithm)
    assert impact["rerouted"] is True and "different route" in impact["reason"]
    assert [int(n) for n in impact["without_live_incidents"]["routes"][0]["node_sequence"]] == STRAIGHT
    assert [int(n) for n in impact["with_incidents"]["routes"][0]["node_sequence"]] != STRAIGHT
    assert [e["provider_id"] for e in impact["incidents_on_original_route"]] == ["C"]
    assert impact["incidents_on_new_route"] == [] and impact["delta"]["travel_time_min"] > 0


def test_impact_without_any_incident_says_so():
    impact = compute_incident_impact(optimizer_for(corridor_graph(), IncidentManager()), A, B)
    assert impact["rerouted"] is False and "No active incident" in impact["reason"]


def test_manual_incident_functionality_is_unchanged():
    g, mgr = corridor_graph(), IncidentManager()
    opt = optimizer_for(g, mgr)
    inc = mgr.add(node(0, 1), node(0, 2), kind="road_closure", severity=1.0, expires_in_seconds=300, description="manual test")
    assert inc["source"] == "manual" and mgr.counts()["manual"] == 1 and mgr.feed()[0]["status"] == "applied"
    assert seq(opt.route_nodes(A, B)) != STRAIGHT
    assert mgr.remove(inc["id"]) is True and mgr.remove(inc["id"]) is False
    assert seq(opt.route_nodes(A, B)) == STRAIGHT
    short = mgr.add(node(0, 1), node(0, 2), expires_in_seconds=0)
    assert mgr.get(short["id"]) is None and mgr.list_active() == []           # expiry still works
    with pytest.raises(ValueError):
        mgr.add(node(0, 1), node(0, 2), kind="meteor")
    with pytest.raises(ValueError):
        mgr.add(node(0, 1), node(0, 2), source="google")
    with pytest.raises(ValueError):
        mgr.add(node(0, 1), node(0, 2), severity=1.5)
    mgr.add(node(0, 1), node(0, 2), bidirectional=True, source="simulation")
    assert (str(node(0, 2)), str(node(0, 1))) in mgr.edge_effects()
    assert mgr.clear("simulation") == 1 and mgr.list_active() == []
