"""Geocoder tests. No internet: Nominatim is replaced by an injected fetcher that records its requests."""
from __future__ import annotations

import networkx as nx

from routing.geocoding import GeocodeError, Geocoder, graph_bbox, road_name_index, spelling_variants, strip_city

BBOX = (85.75, 20.19, 85.90, 20.37)


def _row(name, lat, lon, display=None):
    return {"name": name, "lat": str(lat), "lon": str(lon), "display_name": display or f"{name}, Bhubaneswar, Khordha, Odisha, India",
            "osm_type": "node", "osm_id": 1, "category": "place"}


class FakeNominatim:
    def __init__(self, rows_for=None, fail=False):
        self.rows_for, self.fail, self.calls = rows_for or {}, fail, []

    def __call__(self, url, params, headers, timeout):
        self.calls.append((url, dict(params), dict(headers)))
        if self.fail:
            raise ConnectionError("simulated: name resolution failed")
        text = (params.get("q") or params.get("street") or "").lower()
        for key, rows in self.rows_for.items():
            if key in text:
                return rows
        return []


def _geocoder(fetch, road_names=None):
    return Geocoder(bbox=BBOX, road_names=road_names, fetch=fetch, min_interval_s=0.0)


def test_request_is_restricted_to_india_and_graph_bbox_with_identifying_user_agent():
    fake = FakeNominatim({"jharapada": [_row("Jharapada", 20.2745, 85.8620)]})
    result = _geocoder(fake).geocode("Jharapada, Bhubaneswar")
    assert result.source == "nominatim" and abs(result.latitude - 20.2745) < 1e-9
    _, params, headers = fake.calls[0]
    assert params["countrycodes"] == "in" and params["bounded"] == 1
    assert params["viewbox"] == "85.75,20.37,85.9,20.19"
    assert "TrafficIQ" in headers["User-Agent"]


def test_spelling_variant_is_tried_when_first_spelling_has_no_hit():
    # Only the 'badagada' spelling exists in the (fake) index.
    fake = FakeNominatim({"badagada": [_row("Badagada", 20.2890, 85.8140)]})
    result = _geocoder(fake).geocode("Baragada")
    assert result.query_used == "badagada" and result.match_quality >= 0.55
    assert any(a["step"] == "nominatim_free_text" and a["outcome"] == "no_results" for a in result.attempts)


def test_local_road_name_is_used_without_any_network_call():
    fake = FakeNominatim(fail=True)
    result = _geocoder(fake, {"janpath": (20.2650, 85.8400)}).geocode("Janpath, Bhubaneswar")
    assert result.source == "road_graph_name" and fake.calls == []


def test_result_outside_the_road_network_is_rejected():
    fake = FakeNominatim({"puri": [_row("Puri", 19.8135, 85.8312)]})
    try:
        _geocoder(fake).geocode("Puri")
    except GeocodeError as exc:
        assert exc.code == "not_found"
        assert any(a.get("outcome") == "outside_network" for a in exc.attempts)
    else:
        raise AssertionError("expected GeocodeError")


def test_service_failure_is_reported_as_unavailable_not_as_not_found():
    try:
        _geocoder(FakeNominatim(fail=True)).geocode("Jharapada")
    except GeocodeError as exc:
        assert exc.code == "geocoder_unavailable" and "not proof" in str(exc)
    else:
        raise AssertionError("expected GeocodeError")


def test_unrelated_low_similarity_hit_is_not_accepted():
    fake = FakeNominatim({"jharapada": [_row("Completely Different Place", 20.30, 85.82)]})
    try:
        _geocoder(fake).geocode("Jharapada")
    except GeocodeError as exc:
        assert exc.code == "not_found"
        assert any(a.get("outcome") == "low_similarity" for a in exc.attempts)
    else:
        raise AssertionError("expected GeocodeError")


def test_empty_query_and_city_suffix_handling():
    try:
        _geocoder(FakeNominatim()).geocode("   ")
    except GeocodeError as exc:
        assert exc.code == "empty_query"
    assert strip_city("Jharapada, Bhubaneswar, Odisha, India") == "Jharapada"
    assert spelling_variants("Baragada")[0] == "baragada" and "badagada" in spelling_variants("Baragada")


def test_road_name_index_uses_only_graph_attributes():
    g = nx.MultiDiGraph()
    g.add_node(1, y=20.30, x=85.80); g.add_node(2, y=20.31, x=85.81); g.add_node(3, y=20.32, x=85.82)
    g.add_edge(1, 2, name="Test Road"); g.add_edge(2, 3, name=["Test Road", "Alias Road"]); g.add_edge(3, 1)
    idx = road_name_index(g)
    assert set(idx) == {"test road", "alias road"}
    assert graph_bbox(g) == (85.80, 20.30, 85.82, 20.32)
