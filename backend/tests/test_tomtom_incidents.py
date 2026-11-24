"""TomTom Incident Details client: parsing, status model, error handling. Mocked HTTP only (no key, no internet)."""
import httpx
import pytest

from services.tomtom_incidents import (FIELDS, INCIDENT_PATH, IncidentConfig, normalize_incident, parse_bbox)
from tests.incident_helpers import Clock, FakeTomTom, incident, make_service, payload, row0_line

KEY = "SECRET-KEY-123"


def test_successful_response_is_parsed_with_all_documented_fields():
    fake = FakeTomTom(payload(
        incident("a1", icon=1, magnitude=3, description="Accident", roadNumbers=["NH16"], numberOfReports=4,
                 lastReportTime="2026-11-20T05:00:00Z"),
        incident("c1", icon=8, magnitude=4, description="Closed"),
        incident("w1", icon=9, magnitude=2, description="Roadworks"),
        incident("j1", icon=6, magnitude=1, description="Queuing traffic")))
    svc = make_service(fake, key=KEY)
    out = svc.refresh()
    assert out.fetched and not out.throttled
    by_id = {i["provider_id"]: i for i in out.incidents}
    acc, closed, works, jam = by_id["a1"], by_id["c1"], by_id["w1"], by_id["j1"]
    assert (acc["kind"], acc["category"], acc["severity"], acc["severity_source"]) == ("accident", "Accident", 0.9, "provider_magnitude")
    assert acc["road_numbers"] == ["NH16"] and acc["number_of_reports"] == 4 and acc["last_report_time"].startswith("2026-11-20T05:00")
    assert (closed["kind"], closed["severity"]) == ("road_closure", 1.0)
    assert works["kind"] == "road_work" and works["severity"] == 0.6
    assert jam["kind"] == "traffic_jam" and jam["severity"] == 0.3
    assert acc["start_time"] == "2026-11-20T04:00:00+00:00" and acc["end_time"] == "2026-11-20T18:00:00+00:00"
    assert acc["geometry"]["type"] == "LineString" and acc["latitude"] and acc["longitude"]
    assert svc.status()["state"] == "live" and svc.status()["is_live"] is True


def test_request_uses_documented_endpoint_and_parameters():
    fake = FakeTomTom(payload())
    make_service(fake, key=KEY, language="en-GB").refresh()
    req = fake.requests[0]
    assert req.url.path == INCIDENT_PATH == "/traffic/services/5/incidentDetails"
    q = dict(req.url.params)
    assert q["key"] == KEY and q["timeValidityFilter"] == "present" and q["language"] == "en-GB"
    assert q["bbox"] == "85.74,20.22,85.92,20.37" and q["fields"] == FIELDS
    assert req.headers["Accept-Encoding"] == "gzip"


def test_empty_incident_response_is_a_valid_live_result():
    svc = make_service(FakeTomTom(payload()), key=KEY)
    out = svc.refresh()
    st = svc.status()
    assert out.fetched and out.incidents == []
    assert st["state"] == "live_empty" and st["is_live"] is True and st["incident_count"] == 0
    assert "no incidents" in st["message"].lower()


def test_missing_api_key_disables_service_without_any_request():
    fake = FakeTomTom(payload(incident()))
    svc = make_service(fake, key="")
    out = svc.refresh()
    st = svc.status()
    assert out.throttled and not out.fetched and out.incidents == []
    assert fake.requests == []
    assert st["state"] == "disabled_no_key" and st["configured"] is False and st["is_live"] is False


def test_disabled_by_config_makes_no_request():
    fake = FakeTomTom(payload(incident()))
    svc = make_service(fake, key=KEY, enabled=False)
    svc.refresh()
    assert fake.requests == [] and svc.status()["state"] == "disabled"


@pytest.mark.parametrize("bad", [
    httpx.Response(200, text="<html>not json</html>"),
    httpx.Response(200, json=["not", "a", "dict"]),
    httpx.Response(200, json={"foo": "bar"}),
    httpx.Response(200, json={"incidents": "nope"}),
    httpx.Response(200, json={"detailedError": {"code": "INVALID_REQUEST", "message": "Unknown field"}}),
    httpx.Response(200, json={"incidents": [{"garbage": 1}, "x", None]}),   # nothing parseable at all
])
def test_invalid_api_responses_are_errors_not_empty_feeds(bad):
    svc = make_service(FakeTomTom(bad), key=KEY)
    out = svc.refresh()
    st = svc.status()
    assert not out.fetched
    assert st["state"] == "unavailable" and st["last_error"]["type"] == "invalid_response"
    assert st["incident_count"] == 0 and st["is_live"] is False


def test_a_few_malformed_incidents_are_skipped_but_valid_ones_kept():
    good = incident("ok")
    svc = make_service(FakeTomTom(payload(good, {"properties": {"id": "bad"}}, incident("bad2", coords=[[999, 999], [1, 1]]))), key=KEY)
    out = svc.refresh()
    assert [i["provider_id"] for i in out.incidents] == ["ok"] and svc.status()["skipped_invalid_incidents"] == 2


@pytest.mark.parametrize("code,kind", [(401, "auth"), (403, "auth"), (429, "rate_limited"), (500, "server_error"),
                                       (503, "server_error"), (400, "bad_request"), (404, "http_error")])
def test_http_errors_are_classified_and_never_leak_the_key(code, kind):
    svc = make_service(FakeTomTom(httpx.Response(code, text=f"oops {KEY}")), key=KEY)
    svc.refresh()
    st = svc.status()
    assert st["state"] == "unavailable" and st["last_error"]["type"] == kind and st["last_error"]["http_status"] == code
    assert KEY not in repr(st)


def test_timeout_and_network_failures():
    t = make_service(FakeTomTom(httpx.ConnectTimeout("slow")), key=KEY)
    t.refresh()
    assert t.status()["last_error"]["type"] == "timeout" and t.status()["state"] == "unavailable"
    n = make_service(FakeTomTom(httpx.ConnectError(f"dns failure for https://x?key={KEY}")), key=KEY)
    n.refresh()
    assert n.status()["last_error"]["type"] == "network" and KEY not in repr(n.status())


def test_failure_after_success_keeps_previous_incidents_as_cached_data():
    clock = Clock()
    fake = FakeTomTom(payload(incident("keep")), httpx.Response(500))
    svc = make_service(fake, key=KEY, clock=clock)
    svc.refresh()
    clock.advance(61)
    out = svc.refresh()
    st = svc.status()
    assert not out.fetched and [i["provider_id"] for i in out.incidents] == ["keep"]
    assert st["state"] == "cached" and st["using_cached_data"] is True and st["is_live"] is False
    assert st["last_error"]["type"] == "server_error" and st["incident_count"] == 1


def test_429_honours_retry_after_and_backs_off_without_hitting_the_api():
    clock = Clock()
    fake = FakeTomTom(httpx.Response(429, headers={"Retry-After": "120"}), payload())
    svc = make_service(fake, key=KEY, clock=clock)
    svc.refresh()
    clock.advance(30)
    assert svc.refresh().throttled and len(fake.requests) == 1          # still backing off
    clock.advance(100)
    assert svc.refresh().fetched and len(fake.requests) == 2            # back-off over (130 s > 120 s)


def test_min_refresh_interval_prevents_request_bursts():
    clock = Clock()
    fake = FakeTomTom(payload())
    svc = make_service(fake, key=KEY, clock=clock, min_refresh_seconds=10)
    assert svc.refresh().fetched
    clock.advance(3)
    assert svc.refresh().throttled and len(fake.requests) == 1
    clock.advance(10)
    assert svc.refresh().fetched and len(fake.requests) == 2


def test_cached_state_after_fresh_window_expires_and_staleness_flag():
    clock = Clock()
    svc = make_service(FakeTomTom(payload(incident())), key=KEY, clock=clock, stale_after_seconds=900)
    svc.refresh()
    assert svc.status()["state"] == "live"
    clock.advance(500)
    st = svc.status()
    assert st["state"] == "cached" and st["stale"] is False
    clock.advance(500)
    assert svc.status()["stale"] is True


def test_missing_optional_fields_are_handled():
    raw = {"type": "Feature", "properties": {"iconCategory": 8},
           "geometry": {"type": "Point", "coordinates": [85.82, 20.29]}}
    inc, reason = normalize_incident(raw)
    assert reason is None and inc["provider_id"].startswith("synthetic-") and inc["provider_id_synthetic"] is True
    assert inc["start_time"] is None and inc["end_time"] is None and inc["from"] is None and inc["road_numbers"] == []
    assert inc["description"] == "Road closed" and inc["kind"] == "road_closure" and inc["confirmed"] is True
    again, _ = normalize_incident(raw)
    assert again["provider_id"] == inc["provider_id"]                     # synthetic id is stable across polls


def test_unknown_category_and_probabilities():
    inc, _ = normalize_incident(incident("u", icon=99, magnitude=0, probability="risk_of"))
    assert inc["kind"] == "hazard" and inc["category"] == "Category 99" and inc["severity_source"] == "default_for_kind"
    assert inc["confirmed"] is False
    assert normalize_incident(incident("p", probability="probable"))[0]["confirmed"] is True


def test_bbox_validation_and_env_config(monkeypatch):
    assert parse_bbox("85.7,20.2,85.9,20.4") == (85.7, 20.2, 85.9, 20.4)
    for bad in ("1,2,3", "a,b,c,d", "86,20,85,21", "0,0,50,50"):       # last one exceeds 10,000 km2
        with pytest.raises(ValueError):
            parse_bbox(bad)
    monkeypatch.setenv("TOMTOM_INCIDENTS_BBOX", "85.7,20.2,85.9,20.4")
    monkeypatch.setenv("TOMTOM_INCIDENTS_POLL_INTERVAL", "5")           # below the safe minimum
    cfg = IncidentConfig.from_env()
    assert cfg.bbox == (85.7, 20.2, 85.9, 20.4) and cfg.poll_interval_seconds == 30.0
    monkeypatch.setenv("TOMTOM_INCIDENTS_BBOX", "garbage")
    assert IncidentConfig.from_env().bbox == (85.74, 20.22, 85.92, 20.37)


def test_status_never_contains_the_api_key():
    svc = make_service(FakeTomTom(payload(incident())), key=KEY)
    svc.refresh()
    assert KEY not in repr(svc.status()) and KEY not in repr(svc.current_incidents())
