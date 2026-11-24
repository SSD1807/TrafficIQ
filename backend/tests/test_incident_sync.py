"""Registry synchronisation: dedupe, updates, expiry, failure safety, preserved manual incidents, unmatched handling."""
import httpx
import pytest

from routing.incident_manager import IncidentManager
from tests.incident_helpers import (FakeTomTom, corridor_graph, incident, make_sync, node, patch_manager_clock, payload,
                                    row0_line)

FAR = [[85.8245 + 1.5 * 0.004, 20.2961 + 0.002]]


def tomtom_items(manager):
    return [i for i in manager.list_active() if i["source"] == "tomtom"]


def test_polling_the_same_incident_repeatedly_never_duplicates():
    fake = FakeTomTom(payload(incident("A")))
    sync, svc, mgr, _, clock = make_sync(fake)
    for _ in range(5):
        sync.refresh()
        clock.advance(61)
    assert len(fake.requests) == 5
    assert mgr.counts()["tomtom"] == 1 and len(tomtom_items(mgr)) == 1
    assert tomtom_items(mgr)[0]["id"] == f"tomtom:A:{node(0, 1)}:{node(0, 2)}:0"
    assert sync.status()["last_sync"]["unchanged"] == 1


def test_duplicate_ids_inside_one_response_are_collapsed():
    sync, _, mgr, _, _ = make_sync(FakeTomTom(payload(incident("A"), incident("A"), incident("B", coords=row0_line(2.0, 3.0)))))
    sync.refresh()
    assert mgr.counts()["tomtom"] == 2


def test_changed_provider_information_updates_in_place():
    clock_fake = FakeTomTom(payload(incident("A", icon=1, magnitude=1, description="Slow accident")),
                            payload(incident("A", icon=1, magnitude=3, description="Serious accident, lanes blocked")))
    sync, _, mgr, _, clock = make_sync(clock_fake)
    sync.refresh()
    first = mgr.feed()[0]
    clock.advance(61)
    sync.refresh()
    feed = mgr.feed()
    assert len(feed) == 1 and feed[0]["severity"] == 0.9 and "Serious" in feed[0]["description"]
    assert feed[0]["created_at"] == first["created_at"]                       # same incident, first_seen preserved
    assert feed[0]["updated_at"] >= first["updated_at"] and sync.status()["last_sync"]["updated"] == 1
    assert len(tomtom_items(mgr)) == 1 and tomtom_items(mgr)[0]["severity"] == 0.9


def test_incident_moving_to_another_edge_replaces_the_old_edge_record():
    fake = FakeTomTom(payload(incident("A", icon=1, magnitude=2)), payload(incident("A", icon=1, magnitude=2, coords=row0_line(2.0, 3.0))))
    sync, _, mgr, _, clock = make_sync(fake)
    sync.refresh(); clock.advance(61); sync.refresh()
    assert [(i["edge_u"], i["edge_v"]) for i in tomtom_items(mgr)] == [(str(node(0, 2)), str(node(0, 3)))]


def test_incident_no_longer_reported_is_removed_and_manual_ones_survive():
    fake = FakeTomTom(payload(incident("A"), incident("B", coords=row0_line(2.0, 3.0))), payload(incident("B", coords=row0_line(2.0, 3.0))), payload())
    sync, _, mgr, _, clock = make_sync(fake)
    manual = mgr.add(node(1, 0), node(1, 1), kind="accident", source="manual", description="my test")
    sim = mgr.add(node(1, 1), node(1, 2), kind="hazard", source="simulation")
    sync.refresh()
    assert mgr.counts()["tomtom"] == 2
    clock.advance(61); sync.refresh()
    assert {e["provider_id"] for e in mgr.feed() if e["source"] == "tomtom"} == {"B"} and sync.status()["last_sync"]["removed"] == 1
    clock.advance(61); sync.refresh()                                         # an EMPTY successful response is valid: all gone
    assert mgr.counts()["tomtom"] == 0 and tomtom_items(mgr) == []
    assert mgr.get(manual["id"]) and mgr.get(sim["id"]) and mgr.counts()["manual"] == 1 and mgr.counts()["simulation"] == 1


@pytest.mark.parametrize("failure", [httpx.Response(500), httpx.Response(429), httpx.ConnectTimeout("t"), httpx.Response(200, text="junk"),
                                     httpx.Response(403)])
def test_a_failed_request_never_erases_previous_incidents(failure):
    fake = FakeTomTom(payload(incident("A")), failure)
    sync, svc, mgr, _, clock = make_sync(fake)
    sync.refresh()
    clock.advance(61)
    result = sync.refresh()
    assert result["refreshed"] is False
    assert mgr.counts()["tomtom"] == 1 and len(tomtom_items(mgr)) == 1
    assert result["provider_status"]["state"] == "cached" and result["provider_status"]["last_error"]


def test_stale_cached_incidents_stop_affecting_routing_but_stay_visible(monkeypatch):
    fake = FakeTomTom(payload(incident("A")), httpx.Response(500))
    sync, svc, mgr, _, clock = make_sync(fake, stale_after_seconds=300)
    patch_manager_clock(monkeypatch, clock)
    sync.refresh()
    assert (str(node(0, 1)), str(node(0, 2))) in mgr.edge_effects()
    clock.advance(400)
    sync.refresh()                                                            # fails; data now older than stale_after
    assert mgr.edge_effects() == {}                                           # not priced any more
    f = mgr.feed()[0]
    assert f["stale"] is True and f["status"] == "stale" and f["routing_applied"] is False
    assert sync.status()["state"] == "cached" and sync.status()["stale"] is True


def test_unmatched_incident_is_listed_but_never_applied_to_a_road():
    sync, _, mgr, _, _ = make_sync(FakeTomTom(payload(incident("U", icon=8, geometry_type="Point", coords=[85.8245 + 1.5 * 0.004, 20.2961 + 0.002]))))
    sync.refresh()
    f = mgr.feed()[0]
    assert f["source"] == "tomtom" and f["matched"] is False and f["status"] == "unmatched" and f["edges"] == []
    assert f["match"]["reason"] == "no_nearby_road" and f["routing_applied"] is False
    assert mgr.list_active() == [] and mgr.edge_effects() == {}
    assert mgr.counts()["tomtom_unmatched"] == 1 and mgr.counts()["tomtom_applied_to_routing"] == 0


def test_unconfirmed_and_future_incidents_are_matched_but_not_applied():
    fake = FakeTomTom(payload(incident("R", probability="risk_of"), incident("F", coords=row0_line(2.0, 3.0), timeValidity="future")))
    sync, _, mgr, _, _ = make_sync(fake)
    sync.refresh()
    by_id = {e["provider_id"]: e for e in mgr.feed()}
    assert by_id["R"]["status"] == "matched_not_applied" and "risk_of" in by_id["R"]["routing_skip_reason"]
    assert by_id["F"]["status"] == "matched_not_applied" and "future" in by_id["F"]["routing_skip_reason"]
    assert mgr.edge_effects() == {}


def test_synthetic_fallback_graph_is_never_matched_to_provider_coordinates():
    from services.incident_sync import IncidentSyncService
    from tests.incident_helpers import make_service
    svc = make_service(FakeTomTom(payload(incident("A"))))
    mgr = IncidentManager()
    sync = IncidentSyncService(svc, mgr, lambda: (corridor_graph(), "synthetic_fallback", False))
    sync.refresh()
    f = mgr.feed()[0]
    assert f["status"] == "unmatched" and f["match"]["reason"] == "road_graph_not_openstreetmap" and mgr.edge_effects() == {}


def test_original_provider_data_is_stored_next_to_the_match():
    sync, _, mgr, _, _ = make_sync(FakeTomTom(payload(incident("A", icon=1, magnitude=2, roadNumbers=["NH16"]))))
    sync.refresh()
    f = mgr.feed()[0]
    assert f["provider_id"] == "A" and f["provider"]["category"] == "Accident" and f["provider"]["road_numbers"] == ["NH16"]
    assert f["start_time"] and f["end_time"] and f["geometry"]["type"] == "LineString"
    assert f["match"]["edges"][0]["road_name"] == "Corridor Road" and f["match"]["confidence"] > 0.5


def test_provider_incidents_cannot_be_cleared_by_clearing_manual_ones():
    sync, _, mgr, _, _ = make_sync(FakeTomTom(payload(incident("A"))))
    mgr.add(node(1, 0), node(1, 1), source="manual")
    sync.refresh()
    assert mgr.clear("manual") == 1 and mgr.counts()["tomtom"] == 1


def test_refresh_without_key_does_nothing_and_keeps_manual_incidents():
    fake = FakeTomTom(payload(incident("A")))
    sync, _, mgr, _, _ = make_sync(fake, key="")
    mgr.add(node(1, 0), node(1, 1), source="manual")
    out = sync.refresh()
    assert out["throttled"] and not out["refreshed"] and fake.requests == []
    assert out["provider_status"]["state"] == "disabled_no_key" and out["counts"]["tomtom"] == 0 and out["counts"]["manual"] == 1
