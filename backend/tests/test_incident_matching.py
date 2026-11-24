"""Incident-to-road matching: coordinates, direction, parallel roads, unmatched handling."""
import pytest

from routing.flow_matching import EdgeIndex
from routing.incident_matching import (MatchConfig, R_AMBIGUOUS, R_BAD_GEOMETRY, R_DIRECTION, R_NO_ROAD, R_OUTSIDE, match_incident)
from tests.incident_helpers import (LAT0, LON0, M_LAT, STEP, corridor_graph, node, parallel_roads_graph, row0_line)


@pytest.fixture(scope="module")
def g():
    return corridor_graph()


@pytest.fixture(scope="module")
def idx(g):
    return EdgeIndex.build(g)


def line(coords):
    return {"type": "LineString", "coordinates": coords}


def edge_pairs(res):
    return {(e["u"], e["v"]) for e in res.edges}


def test_linestring_on_a_road_matches_the_correct_directed_edge(g, idx):
    res = match_incident(g, idx, line(row0_line(1.0, 2.0)))
    assert res.matched and res.confidence > 0.8
    assert edge_pairs(res) == {(str(node(0, 1)), str(node(0, 2)))}
    assert res.edges[0]["road_name"] == "Corridor Road" and res.direction_used is True


def test_direction_selects_the_carriageway_the_incident_is_on(g, idx):
    east = match_incident(g, idx, line(row0_line(1.0, 2.0)))
    west = match_incident(g, idx, line(row0_line(2.0, 1.0)))
    assert edge_pairs(east) == {(str(node(0, 1)), str(node(0, 2)))}
    assert edge_pairs(west) == {(str(node(0, 2)), str(node(0, 1)))}        # the opposite directed edge, never both


def test_long_incident_spanning_several_edges_matches_each_edge_once(g, idx):
    res = match_incident(g, idx, line(row0_line(0.0, 3.0, n=40)))
    assert edge_pairs(res) == {(str(node(0, c)), str(node(0, c + 1))) for c in range(3)}


def test_short_incident_inside_a_long_edge_still_matches(g, idx):
    res = match_incident(g, idx, line(row0_line(1.45, 1.55, n=3)))          # ~40 m of a 410 m edge
    assert res.matched and edge_pairs(res) == {(str(node(0, 1)), str(node(0, 2)))}


def test_point_incident_matches_nearest_road_and_flags_both_directions(g, idx):
    pt = {"type": "Point", "coordinates": [LON0 + 1.5 * STEP, LAT0 + 6 * M_LAT]}
    res = match_incident(g, idx, pt)
    assert res.matched and res.bidirectional is True and res.direction_used is False
    assert len(res.edges) == 1 and res.edges[0]["road_name"] == "Corridor Road"


def test_incident_far_from_any_road_is_unmatched(g, idx):
    # Mid-block between row 0 and row 1, ~200 m from every road, but inside the graph's bounding box.
    pt = {"type": "Point", "coordinates": [LON0 + 1.5 * STEP, LAT0 + STEP / 2]}
    res = match_incident(g, idx, pt)
    assert not res.matched and res.reason == R_NO_ROAD and res.edges == [] and res.reason_text


def test_incident_outside_the_graph_is_unmatched(g, idx):
    res = match_incident(g, idx, {"type": "Point", "coordinates": [77.2, 28.6]})
    assert not res.matched and res.reason == R_OUTSIDE


def test_crossing_geometry_with_wrong_direction_is_rejected_not_forced_onto_the_road(g, idx):
    x = LON0 + 1.5 * STEP                                                    # a north-south line crossing Corridor Road
    res = match_incident(g, idx, line([[x, LAT0 - 25 * M_LAT], [x, LAT0 + 25 * M_LAT]]))
    assert not res.matched and res.reason == R_DIRECTION


def test_direction_awareness_can_be_switched_off(g, idx):
    x = LON0 + 1.5 * STEP
    res = match_incident(g, idx, line([[x, LAT0 - 25 * M_LAT], [x, LAT0 + 25 * M_LAT]]), config=MatchConfig(direction_aware=False))
    assert res.matched and res.direction_used is False


def test_parallel_roads_are_ambiguous_and_not_guessed():
    pg = parallel_roads_graph(gap_m=10.0)
    idx = EdgeIndex.build(pg)
    mid = LAT0 + 5 * M_LAT                                                    # exactly between the two roads
    res = match_incident(pg, idx, line([[LON0 + 0.3 * STEP, mid], [LON0 + 0.6 * STEP, mid]]))
    assert not res.matched and res.reason == R_AMBIGUOUS and res.edges == []


def test_parallel_roads_clearly_closer_one_wins():
    pg = parallel_roads_graph(gap_m=40.0)
    idx = EdgeIndex.build(pg)
    res = match_incident(pg, idx, line([[LON0 + 0.3 * STEP, LAT0 + 2 * M_LAT], [LON0 + 0.6 * STEP, LAT0 + 2 * M_LAT]]))
    assert res.matched and {e["road_name"] for e in res.edges} == {"Main Road"}


def test_road_number_hint_resolves_a_parallel_road_ambiguity():
    pg = parallel_roads_graph(gap_m=10.0)
    idx = EdgeIndex.build(pg)
    mid = LAT0 + 5 * M_LAT
    geom = line([[LON0 + 0.3 * STEP, mid], [LON0 + 0.6 * STEP, mid]])
    res = match_incident(pg, idx, geom, road_numbers=["NH 16"])              # normalised: 'NH16' == OSM ref 'NH16'
    assert res.matched and {e["road_name"] for e in res.edges} == {"Main Road"}


@pytest.mark.parametrize("geom", [None, {}, {"type": "Polygon", "coordinates": []}, {"type": "LineString", "coordinates": [[1, 2]]},
                                  {"type": "Point", "coordinates": ["x", "y"]}])
def test_invalid_geometry_is_unmatched_not_an_exception(g, idx, geom):
    res = match_incident(g, idx, geom)
    assert not res.matched and res.reason == R_BAD_GEOMETRY


def test_one_way_graph_only_matches_the_existing_direction():
    ow = corridor_graph(two_way=False)
    idx = EdgeIndex.build(ow)
    ok = match_incident(ow, idx, line(row0_line(1.0, 2.0)))
    wrong = match_incident(ow, idx, line(row0_line(2.0, 1.0)))
    assert ok.matched and not wrong.matched and wrong.reason == R_DIRECTION


def test_provider_road_name_hint_resolves_parallel_ambiguity_only_when_it_names_one_road():
    pg = parallel_roads_graph(gap_m=10.0)
    idx = EdgeIndex.build(pg)
    mid = LAT0 + 5 * M_LAT
    geom = line([[LON0 + 0.3 * STEP, mid], [LON0 + 0.6 * STEP, mid]])
    hinted = match_incident(pg, idx, geom, name_hints=["Main Road", None])
    assert hinted.matched and {e["road_name"] for e in hinted.edges} == {"Main Road"}
    unrelated = match_incident(pg, idx, geom, name_hints=["Some Other Street"])
    assert not unrelated.matched and unrelated.reason == R_AMBIGUOUS


def test_ambiguity_result_names_the_competing_roads():
    pg = parallel_roads_graph(gap_m=10.0)
    idx = EdgeIndex.build(pg)
    mid = LAT0 + 5 * M_LAT
    res = match_incident(pg, idx, line([[LON0 + 0.3 * STEP, mid], [LON0 + 0.6 * STEP, mid]]))
    assert not res.matched and set(res.detail["ambiguous_between"]) == {"Main Road", "Service Road"}
