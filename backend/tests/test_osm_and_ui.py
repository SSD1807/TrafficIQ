"""OSM parsing, map rendering, routing names and dashboard smoke tests.

The OSM fixture (tests/fixtures/mini_grid.osm) is a tiny TEST-ONLY grid so OSMnx
parsing, one-way handling and routing run offline. It is not Bhubaneswar geometry.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "mini_grid.osm"
PAGES = ["Executive Overview", "Traffic Map", "Traffic Prediction", "Forecasting Analytics", "Route Planner",
         "Incident Lab", "Weather", "AI Copilot", "Model Evaluation"]


@pytest.fixture()
def osm_provider(tmp_path):
    from routing.osm_network import OSMNetworkProvider
    prov = OSMNetworkProvider(tmp_path / "g.graphml")
    prov.load_osm_xml(FIXTURE)
    return prov


def test_osm_xml_import_keeps_names_types_and_oneway(osm_provider):
    s = osm_provider.summary()
    assert osm_provider.is_real_osm and s["source"] == "openstreetmap_file"
    assert s["oneway_edges"] == 3 and {"primary", "secondary", "tertiary", "residential"} <= set(s["road_types"])
    assert s["named_edges"] == s["edges"]


def test_cache_roundtrip_and_cache_only_never_downloads(osm_provider, tmp_path):
    from routing.osm_network import OSMNetworkProvider
    again = OSMNetworkProvider(tmp_path / "g.graphml")
    again.load_cached_only()
    assert again.source == "openstreetmap_cached" and again.summary()["oneway_edges"] == 3
    empty = OSMNetworkProvider(tmp_path / "missing.graphml")
    empty.load_cached_only()
    assert empty.source == "synthetic_fallback" and not empty.is_real_osm and "Download" in empty.last_error


def test_failed_download_raises_readable_error_and_keeps_fallback(tmp_path, monkeypatch):
    import osmnx as ox
    from routing.osm_network import OSMNetworkProvider

    def boom(*a, **k):
        raise ConnectionError("overpass unreachable")
    monkeypatch.setattr(ox, "graph_from_place", boom)
    monkeypatch.setattr(ox, "graph_from_point", boom)
    prov = OSMNetworkProvider(tmp_path / "c.graphml")
    prov.load_cached_only()
    with pytest.raises(RuntimeError, match="download failed"):
        prov.download()
    assert prov.source == "synthetic_fallback" and prov.graph is not None


def test_routes_include_road_names_steps_and_real_geometry_flag(osm_provider):
    from routing.route_optimizer import RouteOptimizer
    g = osm_provider.graph
    nodes = list(g.nodes)
    res = RouteOptimizer(g, osm_provider.source).route_nodes(nodes[0], nodes[-1], alternatives=3)
    best = res["routes"][0]
    assert best["is_real_road_geometry"] is True and best["via_roads"] and best["steps"]
    assert abs(sum(s["distance_m"] for s in best["steps"]) / 1000 - best["distance_km"]) < 0.01
    assert all(len(r["polyline_latlon"]) >= 2 for r in res["routes"])


def test_map_renders_roads_routes_incidents_and_legend(osm_provider):
    from routing.incident_manager import IncidentManager
    from routing.route_optimizer import RouteOptimizer
    from ui import maps
    g = osm_provider.graph
    edges = maps.edges_frame(g)
    center, bounds = maps.graph_bounds(edges)
    fmap = maps.base_map(center, 14)
    assert maps.add_roads(fmap, edges, classes=maps.ALL_CLASSES, mode="Road type") > 0
    inc = IncidentManager()
    u, v = next(iter(g.edges()))
    inc.add(u, v, kind="road_closure", severity=1.0)
    assert maps.add_incidents(fmap, g, inc.list_active()) == 1
    nodes = list(g.nodes)
    res = RouteOptimizer(g, osm_provider.source).route_nodes(nodes[0], nodes[-1], alternatives=2)
    maps.add_routes(fmap, res["routes"]); maps.add_legend(fmap, "Simulated congestion")
    import folium
    folium.LayerControl().add_to(fmap)
    html = fmap.get_root().render()
    # regression: the text-path plugin crashed under the canvas renderer and blanked the map
    assert "textpath" not in html.lower() and "PolyLineTextPath" not in html
    assert "oneway-arrow" not in html or "rotate(" in html
    assert "Test Primary Road" in html and "simulated congestion" in html
    assert "Road network" in html and "Incidents" in html  # layer names are JSON-escaped in the page


def test_status_badges_never_call_synthetic_or_simulated_data_live():
    from ui import status as S
    assert S.payload_badge({"status": "ok", "is_live": True}) == S.LIVE
    assert S.payload_badge({"status": "ok", "is_live": False, "source": "x"}) == S.CACHED
    assert S.payload_badge({"status": "simulated_fallback", "is_live": True, "is_simulated": True}) == S.SYNTH
    assert S.payload_badge({"status": "stale_cache", "is_live": True}) == S.CACHED
    assert S.payload_badge({"status": "unavailable"}) == S.UNAVAIL and S.payload_badge(None) == S.UNAVAIL
    assert S.road_badge("synthetic_fallback") == S.SYNTH and S.road_badge("openstreetmap_cached") == S.OSM


def test_forecast_model_selection_and_api_param():
    from fastapi.testclient import TestClient
    from backend.main import app
    c = TestClient(app)
    rf = c.get("/forecasts/Bus Stand", params={"horizon_minutes": 30, "model": "random_forest"}).json()
    xg = c.get("/forecasts/Bus Stand", params={"horizon_minutes": 30, "model": "xgboost"}).json()
    assert rf["regressor_model"] == "random_forest" and xg["regressor_model"] == "xgboost"
    assert c.get("/forecasts/Bus Stand", params={"horizon_minutes": 30, "model": "bogus"}).status_code == 400


def _run_all_pages(at):
    from streamlit.testing.v1 import AppTest  # noqa: F401
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    for page in PAGES:
        at.session_state["nav_page"] = page
        at.run()
        errs = [e.value for e in at.exception] + [e.value for e in at.error
                                                  if "Open-Meteo" not in e.value and "weather" not in e.value.lower()]
        assert not errs, f"{page}: {errs}"


def test_dashboard_all_pages_on_synthetic_fallback_graph(tmp_path):
    from streamlit.testing.v1 import AppTest
    from backend.dependencies import get_provider

    prov = get_provider()
    prov.graph = None
    prov.cache_path = tmp_path / "missing-cache.graphml"

    assert not prov.cache_path.exists()

    _run_all_pages(
        AppTest.from_file(
            str(Path(__file__).parents[1] / "dashboard.py"),
            default_timeout=180,
        )
    )

    assert prov.source == "synthetic_fallback"


def test_dashboard_all_pages_and_route_flow_on_osm_graph():
    from streamlit.testing.v1 import AppTest
    from backend.dependencies import get_provider
    prov = get_provider(); prov.load_osm_xml(FIXTURE, save_to_cache=False)
    try:
        at = AppTest.from_file(str(Path(__file__).parents[1] / "dashboard.py"), default_timeout=180)
        _run_all_pages(at)
        at.session_state["nav_page"] = "Route Planner"; at.run()
        at.radio(key="orig_mode").set_value("Latitude / longitude"); at.radio(key="dest_mode").set_value("Latitude / longitude"); at.run()
        at.number_input(key="orig_lat").set_value(20.2961); at.number_input(key="orig_lon").set_value(85.8245)
        at.number_input(key="dest_lat").set_value(20.3081); at.number_input(key="dest_lon").set_value(85.8365)
        [b for b in at.button if "Find best route" in b.label][0].click().run()
        assert not at.exception
        res = at.session_state["latest_route_result"]
        assert res["routes"] and res["routes"][0]["is_real_road_geometry"] is True
        assert res["_compare"][0]["Travel time (min)"] == res["_compare"][1]["Travel time (min)"]
    finally:
        prov.graph = None; prov.source = "unloaded"


def test_dashboard_route_planner_with_mocked_tomtom_flow(monkeypatch):
    """Checkbox -> probe -> match -> reroute -> provenance card, through the real Streamlit script."""
    import httpx
    from streamlit.testing.v1 import AppTest
    from backend.dependencies import get_provider
    from services.tomtom_service import TomTomService
    import ui.context
    import test_tomtom_routing as T

    handler = lambda req: httpx.Response(200, json=T.row0_segment(current=8, free=50))  # noqa: E731
    svc = TomTomService(api_key="mock-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ui.context, "get_tomtom", lambda: svc)
    prov = get_provider(); prov.load_osm_xml(FIXTURE, save_to_cache=False)
    try:
        at = AppTest.from_file(str(Path(__file__).parents[1] / "dashboard.py"), default_timeout=180)
        at.session_state["nav_page"] = "Route Planner"; at.run()
        assert not at.exception
        at.radio(key="orig_mode").set_value("Latitude / longitude"); at.radio(key="dest_mode").set_value("Latitude / longitude"); at.run()
        at.number_input(key="orig_lat").set_value(T.LAT0); at.number_input(key="orig_lon").set_value(T.LON0 + T.STEP)
        at.number_input(key="dest_lat").set_value(T.LAT0); at.number_input(key="dest_lon").set_value(T.LON0 + 2 * T.STEP)
        tomtom_box = [c for c in at.checkbox if "TomTom" in c.label][0]
        assert tomtom_box.disabled is False
        tomtom_box.check().run()
        [b for b in at.button if "Find best route" in b.label][0].click().run()
        assert not at.exception, [e.value for e in at.exception]
        flow = at.session_state["latest_route_result"]["traffic_flow"]
        assert flow["status"] == "applied" and flow["is_live"] is True and flow["edges_matched"] >= 1
        assert any("Traffic basis" in m.value and "LIVE DATA" in m.value for m in at.markdown)
    finally:
        prov.graph = None; prov.source = "unloaded"


def test_dashboard_never_shows_live_badge_for_traffic_basis_without_key(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from backend.dependencies import get_provider
    import httpx, ui.context
    from services.tomtom_service import TomTomService
    nokey = TomTomService(api_key=None, client=httpx.Client()); nokey.api_key = None
    monkeypatch.setattr(ui.context, "get_tomtom", lambda: nokey)
    prov = get_provider(); prov.load_osm_xml(FIXTURE, save_to_cache=False)
    try:
        at = AppTest.from_file(str(Path(__file__).parents[1] / "dashboard.py"), default_timeout=180)
        at.session_state["nav_page"] = "Route Planner"; at.run()
        box = [c for c in at.checkbox if "TomTom" in c.label][0]
        assert box.disabled is True
        at.radio(key="orig_mode").set_value("Latitude / longitude"); at.radio(key="dest_mode").set_value("Latitude / longitude"); at.run()
        at.number_input(key="dest_lon").set_value(85.8245 + 0.008); at.number_input(key="dest_lat").set_value(20.2961)
        at.number_input(key="orig_lon").set_value(85.8245 + 0.004); at.number_input(key="orig_lat").set_value(20.2961)
        [b for b in at.button if "Find best route" in b.label][0].click().run()
        assert not at.exception
        cards = [m.value for m in at.markdown if "Traffic basis" in m.value]
        assert cards and all("LIVE DATA" not in c for c in cards)
    finally:
        prov.graph = None; prov.source = "unloaded"


def test_one_way_arrows_are_rotated_markers_not_a_plugin(osm_provider):
    from ui import maps
    edges = maps.edges_frame(osm_provider.graph)
    fmap = maps.base_map((20.3, 85.83), 14)
    maps.add_roads(fmap, edges, classes=maps.ALL_CLASSES, mode="Road type")
    html = fmap.get_root().render()
    assert html.count("oneway-arrow") >= 1 and "rotate(" in html and "textpath" not in html.lower()
