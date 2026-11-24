"""End-to-end planning service (geocode -> snap -> incident-aware route) on the REAL cached OSM graph.

Nominatim and TomTom are replaced by fakes (no internet/keys). The coordinates the fake Nominatim returns are taken from
nodes of the loaded graph, so these tests prove the plumbing; they make no claim about where a real place is.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import routing.route_optimizer as ro
from routing.engine import IncidentRoutingEngine
from routing.geocoding import Geocoder, graph_bbox, road_name_index
from routing.incident_manager import IncidentManager
from routing.osm_network import OSMNetworkProvider
from routing.route_optimizer import RouteOptimizer
from services.route_planning import PlanningError, locate, plan_route
from tests.test_incident_engine import (edge_coords, load_provider_incidents, provider_incident_on_route, real, real_pair)
from tests.incident_helpers import incident

CACHE = Path(__file__).resolve().parents[1] / "cache" / "bhubaneswar_drive.graphml"
requires_cache = pytest.mark.skipif(not CACHE.exists(), reason="cached OSM graph not available")
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, state="live"):
        self._state = state

    def status(self):
        return {"state": self._state}


class FakeSync:
    def __init__(self, state="live", fail=False):
        self.provider, self.fail, self.refreshes = FakeProvider(state), fail, 0

    def refresh(self):
        self.refreshes += 1
        if self.fail:
            raise RuntimeError("boom")
        return {"refreshed": True}


def install_geocoder(graph, rows=None, fail=False):
    def fetch(url, params, headers, timeout):
        if fail:
            raise ConnectionError("simulated outage")
        text = (params.get("q") or params.get("street") or "").lower()
        for key, row in (rows or {}).items():
            if key in text:
                return [row]
        return []
    ro._GEOCODERS.clear()
    ro._GEOCODERS[id(graph)] = Geocoder(bbox=graph_bbox(graph), road_names={}, fetch=fetch, min_interval_s=0.0)


def row(name, node, graph):
    return {"name": name, "lat": str(graph.nodes[node]["y"]), "lon": str(graph.nodes[node]["x"]),
            "display_name": f"{name}, Bhubaneswar, Odisha, India", "osm_type": "node", "osm_id": 1, "category": "place"}


def setup(manager=None, **geo):
    graph, _, source = real()
    s, e = real_pair()
    install_geocoder(graph, {"jharapada": row("Jharapada", s, graph), "badagada": row("Badagada", e, graph)} if not geo else None, **geo)
    manager = manager or IncidentManager()
    engine = IncidentRoutingEngine(graph, source, manager)
    return graph, engine, RouteOptimizer(graph, source, manager), manager


def run(engine, optimizer, manager, sync, origin, destination, algorithm="dijkstra", provider=None, **kw):
    prov = provider or OSMNetworkProvider(CACHE)
    return plan_route(engine=engine, optimizer=optimizer, sync=sync, provider=prov, incidents=manager, origin=origin, destination=destination,
                      algorithm=algorithm, alternatives=3, rainfall_mm=0.0, congestion=None, exclude_sources=[],
                      refresh_incidents=kw.pop("refresh", True), departure=NOW, **kw)


@requires_cache
@pytest.mark.parametrize("algo", ["dijkstra", "astar"])
def test_named_origin_and_destination_are_geocoded_snapped_and_routed(algo):
    graph, engine, opt, m = setup()
    out = run(engine, opt, m, FakeSync(), {"name": "Jharapada, Bhubaneswar"}, {"name": "Baragada, Bhubaneswar"}, algo)
    assert out["origin"]["geocode"]["source"] == "nominatim" and out["destination"]["geocode"]["query_used"] == "badagada"
    assert out["origin"]["node_id"] in {str(n) for n in graph.nodes} and out["origin"]["snap_distance_m"] < 5
    assert out["origin"]["road_label"]["label"] and out["recommended_route"]["is_real_road_geometry"]
    assert out["decision"]["action"] == "keep_original" and out["network"]["is_real_osm"]
    assert out["data_sources"]["road_network"] == "OpenStreetMap (cached)"


@requires_cache
def test_live_incident_data_unavailable_is_stated_and_route_is_not_called_incident_free():
    _, engine, opt, m = setup()
    out = run(engine, opt, m, FakeSync(state="unavailable"), {"name": "Jharapada"}, {"name": "Baragada"})
    assert out["incident_data"]["live_data_supported"] is False and out["incident_data"]["incident_free_claim_supported"] is False
    assert "NOT available" in out["incident_data"]["note"] and out["data_sources"]["incidents"] == "TomTom unavailable"


@requires_cache
def test_incident_refresh_failure_does_not_block_routing():
    _, engine, opt, m = setup()
    sync = FakeSync(fail=True)
    out = run(engine, opt, m, sync, {"name": "Jharapada"}, {"name": "Baragada"})
    assert sync.refreshes == 1 and out["incident_data"]["refresh"]["refreshed"] is False and out["recommended_route"]


@requires_cache
@pytest.mark.parametrize("algo", ["dijkstra", "astar"])
def test_live_closure_on_route_triggers_alternative_through_the_service(algo):
    graph, idx, _ = real()
    graph, engine, opt, m = setup()
    path = engine.plan(*real_pair(), algorithm=algo)["original_route"]["node_sequence"]
    raw, edge = provider_incident_on_route(graph, idx, path, "svc-closure", icon=8, magnitude=4)
    load_provider_incidents(graph, idx, m, [raw])
    out = run(engine, opt, m, FakeSync(), {"name": "Jharapada"}, {"name": "Baragada"}, algo)
    rec = [str(n) for n in out["recommended_route"]["node_sequence"]]
    assert out["decision"]["action"] == "reroute" and edge not in set(zip(rec[:-1], rec[1:]))
    assert out["incident_data"]["incident_free_claim_supported"] is False
    assert out["comparison"]["incidents_avoided"] == ["tomtom:svc-closure"]


@requires_cache
def test_geocoder_outage_is_a_503_with_a_reason_not_a_fake_location():
    _, engine, opt, m = setup(fail=True)
    with pytest.raises(PlanningError) as ei:
        run(engine, opt, m, FakeSync(), {"name": "Jharapada"}, {"name": "Baragada"})
    assert ei.value.status == 503 and ei.value.detail["code"] == "geocoder_unavailable"


@requires_cache
def test_unknown_place_is_a_404_with_attempt_log():
    _, engine, opt, m = setup()
    with pytest.raises(PlanningError) as ei:
        run(engine, opt, m, FakeSync(), {"name": "Nowhereville"}, {"name": "Baragada"})
    assert ei.value.status == 404 and ei.value.detail["code"] == "not_found" and ei.value.detail["attempts"]


@requires_cache
def test_coordinates_far_from_any_road_and_outside_the_network_are_rejected():
    graph, engine, opt, m = setup()
    with pytest.raises(PlanningError) as ei:
        locate({"lat": 19.8135, "lon": 85.8312}, graph, opt, engine.labeler)           # Puri: outside the Bhubaneswar graph
    assert ei.value.status == 422 and ei.value.detail["code"] == "outside_network"
    s, _ = real_pair()
    with pytest.raises(PlanningError) as ei:
        locate({"node": "no-such-node"}, graph, opt, engine.labeler)
    assert ei.value.detail["code"] == "unknown_node"


@requires_cache
def test_same_origin_and_destination_is_rejected():
    graph, engine, opt, m = setup()
    s, _ = real_pair()
    with pytest.raises(PlanningError) as ei:
        run(engine, opt, m, FakeSync(), {"node": str(s)}, {"node": str(s)})
    assert ei.value.detail["code"] == "same_node"


@requires_cache
def test_network_geometry_is_real_osm_deduplicated_and_capped():
    from routing.network_geometry import road_segments
    graph, _, _ = real()
    major = road_segments(graph, level="major", limit=100000)
    allr = road_segments(graph, level="all", limit=100000)
    assert 0 < major["count"] < allr["count"] and not major["truncated"]
    assert allr["count"] < graph.number_of_edges()                       # two-way roads are emitted once
    capped = road_segments(graph, level="all", limit=50)
    assert capped["count"] == 50 and capped["truncated"] and capped["total_matching"] == allr["count"]
    box = road_segments(graph, level="all", bbox=(20.27, 85.82, 20.29, 85.84), limit=100000)
    assert 0 < box["count"] < allr["count"]
    assert all(len(s["positions"]) >= 2 for s in box["segments"])
