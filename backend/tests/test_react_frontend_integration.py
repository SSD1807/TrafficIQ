"""Tests for the changes made when merging the React frontend (CORS origins + /models/evaluation)."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import main
from tests.test_api import _offline_dependencies


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173",
                                    "http://localhost:4173", "http://127.0.0.1:4173",
                                    "http://localhost:8501", "http://127.0.0.1:8501"])
def test_cors_allows_vite_and_legacy_streamlit_origins(origin):
    client = TestClient(main.app)
    r = client.options("/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


def test_cors_rejects_unknown_origin():
    client = TestClient(main.app)
    r = client.options("/health", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_simple_get_carries_header_for_vite_origin():
    client = TestClient(main.app)
    r = client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_models_evaluation_serves_saved_artifacts_without_recomputing():
    client = TestClient(main.app)
    r = client.get("/models/evaluation")
    assert r.status_code == 200
    d = r.json()
    assert d["is_live"] is False and d["source"] == "saved_training_artifacts"
    on_disk = json.loads((main.ROOT / "outputs" / "phase1_metrics.json").read_text(encoding="utf-8"))
    assert d["classification"]["holdout_test_metrics"] == on_disk["holdout_test_metrics"]
    assert set(d["forecasting"]["results"]) == {"30m", "60m"}
    assert "synthetic" in d["notice"].lower()
    assert "lstm" in d["not_available"]


def test_models_evaluation_503_when_artifacts_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    client = TestClient(main.app)
    assert client.get("/models/evaluation").status_code == 503


def test_route_response_uses_latitude_longitude_node_fields_and_polyline(monkeypatch):
    _offline_dependencies(monkeypatch)
    client = TestClient(main.app)
    node = client.get("/routing/nodes").json()["nodes"][0]
    assert {"latitude", "longitude"} <= set(node) and "lat" not in node and "lon" not in node
    body = client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8"}).json()
    assert body["routes"][0]["polyline_latlon"] and body["routes"][0]["node_sequence"]


def test_route_request_requires_exactly_one_endpoint_pair(monkeypatch):
    _offline_dependencies(monkeypatch)
    client = TestClient(main.app)
    assert client.post("/routes/calculate", json={}).status_code == 422
    both = {"origin_node": "1", "destination_node": "8", "origin_lat": 20.29, "origin_lon": 85.8,
            "destination_lat": 20.2, "destination_lon": 85.8}
    assert client.post("/routes/calculate", json=both).status_code == 422
    coords = {"origin_lat": 20.2961, "origin_lon": 85.8245, "destination_lat": 20.296, "destination_lon": 85.839}
    assert client.post("/routes/calculate", json=coords).status_code == 200


def test_incident_create_reroute_and_delete_round_trip(monkeypatch):
    _offline_dependencies(monkeypatch)
    client = TestClient(main.app)
    base = client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8"}).json()["routes"][0]["node_sequence"]
    created = client.post("/incidents", json={"edge_u": base[0], "edge_v": base[1], "kind": "accident", "severity": 0.9, "bidirectional": True})
    assert created.status_code == 201
    rerouted = client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8"}).json()
    assert rerouted["active_incident_count"] == 1 and rerouted["routes"][0]["node_sequence"] != base
    assert client.delete(f"/incidents/{created.json()['id']}").status_code == 200
    assert client.delete(f"/incidents/{created.json()['id']}").status_code == 404
    assert client.post("/routes/calculate", json={"origin_node": "1", "destination_node": "8"}).json()["routes"][0]["node_sequence"] == base
