import networkx as nx

from routing.incident_manager import IncidentManager
from routing.osm_network import build_synthetic_fallback_graph
from routing.route_optimizer import RouteOptimizer


def test_dijkstra_and_astar_return_connected_routes_with_same_optimal_cost():
    graph = build_synthetic_fallback_graph()
    optimizer = RouteOptimizer(graph, "synthetic_fallback")
    d = optimizer.route_nodes(1, 8, algorithm="dijkstra", alternatives=2)
    a = optimizer.route_nodes(1, 8, algorithm="astar", alternatives=2)
    assert d["routes"] and a["routes"]
    assert abs(d["routes"][0]["estimated_travel_time_min"] - a["routes"][0]["estimated_travel_time_min"]) < 1e-8
    assert d["routes"][0]["is_real_road_geometry"] is False


def test_road_closure_removes_directed_edge_and_reroutes():
    graph = build_synthetic_fallback_graph()
    incidents = IncidentManager()
    optimizer = RouteOptimizer(graph, "synthetic_fallback", incidents)
    before = optimizer.route_nodes(1, 8, algorithm="dijkstra", alternatives=1)["routes"][0]
    incidents.add(1, 3, kind="road_closure", severity=1.0, expires_in_seconds=300)
    after = optimizer.route_nodes(1, 8, algorithm="dijkstra", alternatives=2)["routes"][0]
    assert before["node_sequence"] != after["node_sequence"]
    assert "3" not in after["node_sequence"] or after["node_sequence"][0] != "1"


def test_incidents_expire_and_can_be_removed():
    manager = IncidentManager()
    item = manager.add("1", "2", kind="accident", expires_in_seconds=3600)
    assert len(manager.list_active()) == 1
    assert manager.remove(item["id"]) is True
    assert manager.list_active() == []
    manager.add("1", "2", kind="accident", expires_in_seconds=0)
    assert manager.list_active() == []


def test_severe_accident_penalty_can_change_selected_route():
    graph = build_synthetic_fallback_graph()
    incidents = IncidentManager()
    optimizer = RouteOptimizer(graph, "synthetic_fallback", incidents)
    before = optimizer.route_nodes(1, 8, algorithm="dijkstra", alternatives=1)["routes"][0]
    incidents.add(1, 3, kind="accident", severity=1.0, expires_in_seconds=300)
    after = optimizer.route_nodes(1, 8, algorithm="dijkstra", alternatives=1)["routes"][0]
    assert before["node_sequence"] != after["node_sequence"]
    assert "1" in after["node_sequence"] and "8" in after["node_sequence"]
    assert after["estimated_travel_time_min"] > before["estimated_travel_time_min"]
