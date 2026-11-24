from fastapi.testclient import TestClient

from backend import main
from routing.incident_manager import IncidentManager
from routing.osm_network import OSMNetworkProvider, build_synthetic_fallback_graph
from routing.route_optimizer import RouteOptimizer


def _offline_dependencies(monkeypatch):
    provider = OSMNetworkProvider()
    provider.graph = build_synthetic_fallback_graph()
    provider.source = "synthetic_fallback"
    incidents = IncidentManager()
    optimizer = RouteOptimizer(provider.graph, provider.source, incidents)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "get_incidents", lambda: incidents)
    monkeypatch.setattr(main, "get_optimizer", lambda: optimizer)
    return provider, incidents, optimizer


def test_routing_nodes_endpoint_marks_fallback_as_not_real(monkeypatch):
    provider, _, _ = _offline_dependencies(monkeypatch)
    client = TestClient(main.app)
    response = client.get("/routing/nodes")
    assert response.status_code == 200
    data = response.json()
    assert data["graph_source"] == "synthetic_fallback"
    assert data["is_real_road_geometry"] is False
    assert len(data["nodes"]) == provider.graph.number_of_nodes()


def test_route_and_incident_endpoints_work_offline(monkeypatch):
    _, incidents, _ = _offline_dependencies(monkeypatch)
    client = TestClient(main.app)
    route = client.post("/routes/calculate", json={
        "origin_node": "1", "destination_node": "8", "algorithm": "astar",
        "alternatives": 2, "use_simulated_congestion": False,
    })
    assert route.status_code == 200
    assert route.json()["routes"]
    assert route.json()["routes"][0]["graph_source"] == "synthetic_fallback"

    created = client.post("/incidents", json={
        "edge_u": "1", "edge_v": "3", "kind": "road_closure",
        "severity": 1.0, "expires_in_seconds": 3600,
    })
    assert created.status_code == 201
    assert len(incidents.list_active()) == 1
    incident_id = created.json()["id"]
    deleted = client.delete(f"/incidents/{incident_id}")
    assert deleted.status_code == 200
    assert incidents.list_active() == []


def test_invalid_route_endpoint_pair_returns_validation_error():
    client = TestClient(main.app)
    response = client.post("/routes/calculate", json={"origin_node": "1"})
    assert response.status_code == 422


def test_route_endpoint_uses_departure_time_for_simulated_costs(monkeypatch):
    _offline_dependencies(monkeypatch)
    client = TestClient(main.app)
    response = client.post("/routes/calculate", json={
        "origin_node": "1", "destination_node": "8", "algorithm": "dijkstra",
        "alternatives": 1, "use_simulated_congestion": True,
        "departure_time": "2026-11-05T08:00:00+05:30",
    })
    assert response.status_code == 200
    result = response.json()
    assert result["departure_time"] == "2026-11-05T08:00:00+05:30"
    assert result["estimated_arrival_time"]
    assert result["traffic_cost_mode"] == "simulated_departure_time_traffic"


def test_saved_classification_pipeline_is_served_through_api():
    client = TestClient(main.app)
    locations = client.get("/locations")
    assert locations.status_code == 200
    configured = locations.json()["locations"]
    assert configured
    prediction = client.post(f"/traffic/predict/{configured[0]}", json={})
    assert prediction.status_code == 200
    body = prediction.json()
    assert body["prediction_horizon_minutes"] == 30
    assert body["predicted_congestion"] in {"Low", "Medium", "High"}
    assert body["is_live"] is False
    assert body["model_name"] == "Random Forest"


def test_saved_numeric_forecast_artifacts_are_served_through_api():
    client = TestClient(main.app)
    configured = client.get("/locations").json()["locations"]
    location = configured[0]
    for horizon in (30, 60):
        response = client.get(f"/forecasts/{location}?horizon_minutes={horizon}")
        assert response.status_code == 200
        body = response.json()
        assert body["horizon_minutes"] == horizon
        assert body["vehicle_count"] is not None
        assert body["avg_speed"] is not None
        assert body["is_live"] is False


def test_health_endpoint_exposes_data_source_provenance(monkeypatch):
    provider, _, _ = _offline_dependencies(monkeypatch)
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["data_sources"]["traffic"]["status"] == "historical_synthetic"
    assert body["data_sources"]["traffic"]["is_live"] is False
    assert body["data_sources"]["road_network"]["source"] == "synthetic_fallback"
    assert body["data_sources"]["road_network"]["is_real_road_geometry"] is False
    assert body["data_sources"]["weather"]["status"] == "not_requested"
    assert "does not perform external provider requests" in body["notice"]
