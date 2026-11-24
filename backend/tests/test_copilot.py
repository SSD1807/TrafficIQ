from copilot.agent import TrafficCopilot
from routing.osm_network import build_synthetic_fallback_graph
from routing.route_optimizer import RouteOptimizer


class FakeTraffic:
    def list_locations(self):
        return []


class FakeForecasts:
    pass


class FakeWeather:
    pass


def test_rule_based_copilot_can_calculate_routes_from_natural_language():
    graph = build_synthetic_fallback_graph()
    optimizer = RouteOptimizer(graph, "synthetic_fallback")
    copilot = TrafficCopilot(
        FakeTraffic(), FakeForecasts(), FakeWeather(), lambda: optimizer, incident_manager=None
    )
    response = copilot.chat("Find the best route from Fallback Central Junction to Fallback East Junction")
    assert response["mode"] == "rule_based"
    assert response["tool_results"]["route"]["routes"]
    assert "distance_km" in response["tool_results"]["route"]["routes"][0]
