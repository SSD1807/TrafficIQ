"""AI Traffic Copilot incident answers: grounded in real tool results, never invented, never calling test data live."""
import httpx
import pytest

from copilot.agent import TrafficCopilot
from tests.incident_helpers import (LAT0, LON0, M_LAT, STEP, FakeTomTom, incident, make_sync, node, optimizer_for, payload)


class Stub:
    def list_locations(self):
        return []


def build(fake, *, key="test-key", **config):
    sync, svc, mgr, graph, clock = make_sync(fake, key=key, **config)
    opt = optimizer_for(graph, mgr)
    places = {"alpha": (LAT0, LON0), "omega": (LAT0, LON0 + 3 * STEP)}
    opt.geocode_place = lambda name: places[name.strip().lower()]
    copilot = TrafficCopilot(Stub(), Stub(), Stub(), lambda: opt, incident_manager=mgr, incident_sync=sync)
    return copilot, mgr, sync, clock


def test_reports_actual_live_incidents_with_details():
    cp, _, _, _ = build(FakeTomTom(payload(incident("A1", icon=1, magnitude=3, description="Multi-vehicle accident"),
                                           incident("P1", icon=9, magnitude=2, description="Roadworks on the flyover",
                                                    coords=[LON0 + 2.5 * STEP, LAT0 + 4 * M_LAT], geometry_type="Point"))))
    r = cp.chat("Are there any current traffic incidents in Bhubaneswar?")
    assert r["mode"] == "rule_based" and "incident_feed" in r["tool_results"]
    assert "TomTom live data" in r["answer"] and "Multi-vehicle accident" in r["answer"] and "Corridor Road" in r["answer"]
    assert "major" in r["answer"] and "applied to routing" in r["answer"]
    feed = r["tool_results"]["incident_feed"]
    assert feed["provider"]["state"] == "live" and {e["provider_id"] for e in feed["incidents"] if e["source"] == "tomtom"} == {"A1", "P1"}
    assert "Roadworks on the flyover" in r["answer"]


def test_empty_live_feed_is_reported_as_no_incidents_without_inventing_any():
    cp, _, _, _ = build(FakeTomTom(payload()))
    r = cp.chat("Show the latest traffic incidents")
    assert "currently reports no incidents" in r["answer"] and r["tool_results"]["incident_feed"]["incidents"] == []


def test_missing_key_says_not_configured_and_never_claims_live_data():
    cp, mgr, _, _ = build(FakeTomTom(payload(incident("A1"))), key="")
    r = cp.chat("Are there any current traffic incidents?")
    assert "not configured" in r["answer"] and "cannot report real-world incidents" in r["answer"]
    assert "TomTom live data" not in r["answer"] and r["tool_results"]["incident_feed"]["incidents"] == []


def test_simulated_and_manual_incidents_are_labelled_as_test_data_not_live():
    cp, mgr, _, _ = build(FakeTomTom(payload()))
    mgr.add(node(1, 0), node(1, 1), kind="accident", source="simulation", description="Demo crash")
    mgr.add(node(1, 1), node(1, 2), kind="accident", source="manual", description="Typed by user")
    r = cp.chat("Where are the reported accidents?")
    assert "TomTom reports no accidents right now" in r["answer"]
    assert "manual/simulated" in r["answer"] and "NOT live" in r["answer"] and "(simulation)" in r["answer"] and "(manual)" in r["answer"]


def test_provider_unavailable_is_reported_honestly():
    cp, _, _, _ = build(FakeTomTom(httpx.Response(500)))
    r = cp.chat("Show the latest traffic incidents")
    assert "temporarily unavailable" in r["answer"] and "no live incident data" in r["answer"]
    assert "TomTom live data" not in r["answer"]


def test_cached_data_is_flagged_as_not_live():
    cp, _, _, clock = build(FakeTomTom(payload(incident("A1", description="Roadworks ahead", icon=9, magnitude=2)), httpx.Response(500)))
    cp.chat("Any incidents right now?")
    clock.advance(61)
    r = cp.chat("Any incidents right now?")
    assert "CACHED" in r["answer"] and "not live" in r["answer"] and "Roadworks ahead" in r["answer"]


def test_closed_roads_question_lists_only_closures():
    cp, _, _, _ = build(FakeTomTom(payload(incident("C1", icon=8, description="Closed"),
                                           incident("W1", icon=9, magnitude=2, description="Roadworks", coords=[LON0 + 2.5 * STEP, LAT0 + 4 * M_LAT],
                                                    geometry_type="Point"))))
    r = cp.chat("Which roads are currently closed?")
    assert "road closure" in r["answer"] and "Closed" in r["answer"] and "Roadworks" not in r["answer"]
    assert cp.chat("Show the latest traffic incidents")["answer"].count("Roadworks") >= 1     # the roadworks incident IS in the feed


def test_incident_on_a_named_route_returns_the_alternative_route():
    cp, _, _, _ = build(FakeTomTom(payload(incident("C1", icon=8, description="Closed for repairs"))))
    r = cp.chat("Is there an incident on the route from Alpha to Omega?")
    impact = r["tool_results"]["route_incident_impact"]
    assert impact["rerouted"] is True and "Closed for repairs" in r["answer"] and "Alternative route" in r["answer"]
    assert impact["incidents_on_original_route"][0]["provider_id"] == "C1"


def test_followup_questions_use_the_last_route_and_astar_can_be_requested():
    cp, _, _, _ = build(FakeTomTom(payload(incident("C1", icon=8))))
    first = cp.chat("Find the best route from Alpha to Omega using A*")
    assert first["tool_results"]["route"]["routes"]
    r = cp.chat("Can you find an alternative route?")
    assert r["tool_results"]["route_incident_impact"]["algorithm"] == "astar" and r["tool_results"]["route_incident_impact"]["rerouted"] is True
    assert "Alpha -> Omega" in r["answer"]


def test_route_followup_without_a_route_asks_for_one_instead_of_guessing():
    cp, _, _, _ = build(FakeTomTom(payload()))
    r = cp.chat("Is there an incident on my selected route?")
    assert "do not have a selected route" in r["answer"] and "route_incident_impact" not in r["tool_results"]


def test_plain_route_requests_still_work_and_are_not_hijacked():
    cp, _, _, _ = build(FakeTomTom(payload(incident("C1", icon=8))))
    r = cp.chat("Find the best route from Alpha to Omega")
    assert "route" in r["tool_results"] and "route_incident_impact" not in r["tool_results"]


def test_copilot_without_incident_service_still_answers_from_the_local_registry():
    from routing.incident_manager import IncidentManager
    from tests.incident_helpers import corridor_graph
    mgr = IncidentManager()
    mgr.add(node(0, 1), node(0, 2), kind="road_closure", source="manual", description="local")
    opt = optimizer_for(corridor_graph(), mgr)
    cp = TrafficCopilot(Stub(), Stub(), Stub(), lambda: opt, incident_manager=mgr)
    r = cp.chat("Are there any incidents?")
    assert "not connected" in r["answer"] and "manual/simulated" in r["answer"]
