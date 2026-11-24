"""OSMnx download -> GraphML cache -> offline reload -> fallback behaviour, against a local mock Overpass/Nominatim."""
from __future__ import annotations

import pytest
import osmnx as ox

from routing.osm_network import OSMNetworkProvider
from mock_osm_server import MockOSM

UNREACHABLE = "http://127.0.0.1:9"  # nothing listens here: any network use after caching would fail


@pytest.fixture()
def osm_settings():
    saved = (ox.settings.nominatim_url, ox.settings.overpass_url, ox.settings.requests_timeout, ox.settings.use_cache,
             ox.settings.overpass_rate_limit)
    ox.settings.requests_timeout = 15
    ox.settings.overpass_rate_limit = False  # OSMnx would otherwise pause 60 s when the status endpoint is unreachable
    yield ox.settings
    (ox.settings.nominatim_url, ox.settings.overpass_url, ox.settings.requests_timeout, ox.settings.use_cache,
     ox.settings.overpass_rate_limit) = saved


def point_at(settings, server):
    settings.nominatim_url = server.base + "/"
    settings.overpass_url = server.base + "/api"


def test_download_by_place_then_cache_reload_works_fully_offline(tmp_path, osm_settings):
    cache = tmp_path / "cache" / "bhubaneswar_drive.graphml"
    with MockOSM() as srv:
        point_at(osm_settings, srv)
        prov = OSMNetworkProvider(cache)
        G = prov.download()
        assert prov.source == "openstreetmap_downloaded" and prov.is_real_osm
        assert prov.query_used.startswith("place:")
        assert cache.exists() and cache.stat().st_size > 1000
        assert any(p.endswith("/search") for p in srv.requests) and any("interpreter" in p for p in srv.requests)
        n, e = G.number_of_nodes(), G.number_of_edges()
        assert prov.summary()["oneway_edges"] > 0 and prov.summary()["named_edges"] > 0
    # server stopped AND endpoints unreachable: a new provider must use the cache without any network
    osm_settings.nominatim_url = osm_settings.overpass_url = UNREACHABLE
    again = OSMNetworkProvider(cache)
    G2 = again.load_cached_only()
    assert again.source == "openstreetmap_cached" and again.is_real_osm
    assert (G2.number_of_nodes(), G2.number_of_edges()) == (n, e)
    assert again.summary()["oneway_edges"] == prov.summary()["oneway_edges"]  # attributes survive the GraphML round-trip


def test_place_geocode_failure_falls_back_to_radius_query(tmp_path, osm_settings):
    with MockOSM(geocode_ok=False) as srv:
        point_at(osm_settings, srv)
        prov = OSMNetworkProvider(tmp_path / "c.graphml")
        prov.download()
        assert prov.query_used.startswith("radius:") and prov.is_real_osm and (tmp_path / "c.graphml").exists()


def test_failed_download_keeps_labelled_fallback_and_writes_no_cache(tmp_path, osm_settings):
    with MockOSM(overpass_ok=False) as srv:
        point_at(osm_settings, srv)
        osm_settings.requests_timeout = 5
        prov = OSMNetworkProvider(tmp_path / "c.graphml")
        prov.load_cached_only()
        assert prov.source == "synthetic_fallback"
        with pytest.raises(RuntimeError, match="download failed"):
            prov.download()
        assert prov.source == "synthetic_fallback" and not prov.is_real_osm and prov.graph is not None
        assert not (tmp_path / "c.graphml").exists() and "place:" in prov.last_error and "radius:" in prov.last_error


def test_failed_refresh_does_not_discard_a_good_cached_graph(tmp_path, osm_settings):
    cache = tmp_path / "c.graphml"
    with MockOSM() as srv:
        point_at(osm_settings, srv); OSMNetworkProvider(cache).download()
    before = cache.read_bytes()
    prov = OSMNetworkProvider(cache); prov.load_cached_only()
    osm_settings.nominatim_url = osm_settings.overpass_url = UNREACHABLE; osm_settings.requests_timeout = 3
    with pytest.raises(RuntimeError):
        prov.download()
    assert prov.source == "openstreetmap_cached" and cache.read_bytes() == before


def test_corrupt_cache_is_reported_and_falls_back_with_synthetic_label(tmp_path):
    cache = tmp_path / "c.graphml"; cache.write_text("<graphml>this is not a valid graph")
    prov = OSMNetworkProvider(cache)
    prov.load_cached_only()
    assert prov.source == "synthetic_fallback" and "unreadable" in prov.last_error
    from ui.status import road_badge, SYNTH
    assert road_badge(prov.source) == SYNTH
