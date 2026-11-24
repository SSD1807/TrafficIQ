"""HERE provider tests with a mocked HTTP layer (payloads follow HERE's documented Traffic v7 / Routing v8 shapes).

These prove parsing, labelling, caching, error handling and key hygiene. They do NOT prove that HERE has live coverage in
Bhubaneswar or that the assumed field semantics (m/s speeds, shape order) hold -- run scripts/live_test_here.py for that.
"""
from __future__ import annotations

import httpx
import pytest

from services.traffic_providers import create_provider
from services.traffic_providers.base import BHUBANESWAR_BBOX
from services.traffic_providers.here_provider import HereProvider, jam_level

KEY = "SECRET-KEY-123"
BBOX = (85.80, 20.28, 85.85, 20.32)


class Resp:
    def __init__(self, status=200, body=None, headers=None, content=b""):
        self.status_code, self._body, self.headers, self.content = status, body, headers or {}, content

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeClient:
    """Scripted responses; records every request so tests can assert on calls and on key placement."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t


def flow_body(speed=1.4, free=13.9, jam=8.5, conf=0.9, traversability="open"):
    return {"sourceUpdated": "2026-11-14T10:00:00Z", "results": [{
        "location": {"description": "NH16", "length": 900, "shape": {"links": [{"points": [
            {"lat": 20.3000, "lng": 85.8100}, {"lat": 20.3000, "lng": 85.8200}], "length": 900}]}},
        "currentFlow": {"speed": speed, "speedUncapped": speed, "freeFlow": free, "jamFactor": jam, "confidence": conf,
                        "traversability": traversability}}]}


def make(*responses, key=KEY, clock=None, **kw):
    client = FakeClient(*responses)
    return HereProvider(api_key=key, client=client, clock=clock or Clock(), **kw), client


# ------------------------------------------------------------------ configuration / auth
def test_missing_key_is_disabled_and_makes_no_request():
    p, c = make(Resp(200, flow_body()), key="")
    r = p.fetch_flow(*BBOX)
    assert (r.status, r.data_state, r.is_live) == ("disabled", "disabled", False) and not c.calls
    assert p.status()["state"] == "disabled" and not p.configured
    assert p.route_eta((20.3, 85.8), (20.31, 85.82)).data_state == "disabled" and p.tile(10, 1, 1).data_state == "disabled" and not c.calls


def test_placeholder_key_counts_as_missing():
    assert not HereProvider(api_key="your_key_here", client=FakeClient()).configured


def test_status_makes_no_external_request():
    p, c = make(Resp(200, flow_body()))
    p.status(); assert not c.calls


def test_key_sent_server_side_as_param_and_never_in_public_output():
    p, c = make(Resp(200, flow_body()))
    r = p.fetch_flow(*BBOX)
    url, params = c.calls[0]
    assert url.startswith("https://data.traffic.hereapi.com/v7/flow") and params["apiKey"] == KEY
    assert params["in"].startswith("bbox:85.800000,20.280000") and params["locationReferencing"] == "shape"
    assert KEY not in str(r.to_public()) and KEY not in str(p.status())


@pytest.mark.parametrize("code", [401, 403])
def test_unauthorized(code):
    p, _ = make(Resp(code, {}))
    r = p.fetch_flow(*BBOX)
    assert (r.status, r.data_state, r.is_live) == ("unauthorized", "unavailable", False)
    assert KEY not in (r.message or "") and "rejected the API key" in r.message


# ------------------------------------------------------------------ live parsing
def test_live_flow_parsed_units_levels_and_measurements():
    p, _ = make(Resp(200, flow_body(speed=1.4, free=13.9, jam=8.5)))
    r = p.fetch_flow(*BBOX)
    assert (r.status, r.data_state, r.is_live) == ("ok", "live", True) and r.source_updated == "2026-11-14T10:00:00Z"
    s = r.segments[0]
    assert s.current_kph == pytest.approx(5.0, abs=0.1) and s.free_flow_kph == pytest.approx(50.0, abs=0.1)
    assert s.level == "severe" and s.path[0] == (20.3, 85.81) and s.description == "NH16"
    m = r.measurements[0]
    assert m.is_live and m.confidence == 0.9 and m.factor == 6.0  # 50/5 = 10x, capped at 6x by flow_matching.MAX_FACTOR
    assert r.usable


def test_closed_road_marked_closed():
    p, _ = make(Resp(200, flow_body(speed=0.0, jam=10, traversability="closed")))
    r = p.fetch_flow(*BBOX)
    assert r.segments[0].level == "closed" and r.measurements[0].road_closure


def test_jam_levels():
    assert [jam_level(j) for j in (0, 2.4, 2.5, 4.9, 5, 7.9, 8, 10)] == ["free", "free", "slow", "slow", "heavy", "heavy", "severe", "severe"]
    assert jam_level(3, closed=True) == "closed"


def test_item_without_confidence_is_displayed_but_not_used_for_costing():
    body = flow_body(); del body["results"][0]["currentFlow"]["confidence"]
    r = make(Resp(200, body))[0].fetch_flow(*BBOX)
    assert len(r.segments) == 1 and r.measurements == [] and not r.usable


def test_empty_results_is_no_data_not_live():
    r = make(Resp(200, {"results": []}))[0].fetch_flow(*BBOX)
    assert (r.status, r.data_state, r.is_live) == ("no_data", "unavailable", False) and "no flow segments" in r.message


def test_malformed_body_is_error_not_live():
    for body in ("not json", {"unexpected": 1}):
        r = make(Resp(200, ValueError("bad json") if body == "not json" else body))[0].fetch_flow(*BBOX)
        assert (r.status, r.is_live, r.data_state) == ("error", False, "unavailable")


# ------------------------------------------------------------------ coverage claim
def test_coverage_not_observed_until_segments_returned_for_bhubaneswar():
    p, _ = make(Resp(200, {"results": []}))
    p.fetch_flow(*BBOX)
    st = p.status(); assert st["coverage_observed"] is False and "NOT confirmed" in st["coverage_note"]
    p2, _ = make(Resp(200, flow_body()))
    assert p2.status()["coverage_observed"] is False  # nothing fetched yet
    p2.fetch_flow(*BBOX)
    assert p2.status()["coverage_observed"] is True


def test_segments_outside_bhubaneswar_do_not_confirm_coverage():
    p, _ = make(Resp(200, flow_body()))
    p.fetch_flow(77.0, 28.5, 77.1, 28.6)  # Delhi
    assert p.status()["coverage_observed"] is False


# ------------------------------------------------------------------ caching / stale / failure
def test_fresh_cache_labelled_cached_with_age_and_no_second_request():
    clock = Clock(); p, c = make(Resp(200, flow_body()), clock=clock, flow_ttl=120)
    assert p.fetch_flow(*BBOX).data_state == "live"
    clock.t += 30
    r = p.fetch_flow(*BBOX)
    assert (r.data_state, r.is_live, r.age_seconds) == ("cached", False, pytest.approx(30)) and len(c.calls) == 1
    clock.t += 200  # past TTL -> refetch
    assert p.fetch_flow(*BBOX).data_state == "live" and len(c.calls) == 2


def test_failure_after_cache_serves_stale_explicitly_not_live_then_unavailable():
    clock = Clock(); p, _ = make(Resp(200, flow_body()), Resp(500, {}), clock=clock, flow_ttl=60, stale_max=600)
    p.fetch_flow(*BBOX)
    clock.t += 120
    r = p.fetch_flow(*BBOX)
    assert (r.data_state, r.is_live) == ("stale_cache", False) and "NOT live" in r.message and r.segments
    clock.t += 1000
    r = p.fetch_flow(*BBOX)
    assert (r.data_state, r.is_live, r.segments) == ("unavailable", False, [])


def test_rate_limit_pauses_requests_and_honours_retry_after():
    clock = Clock(); p, c = make(Resp(429, {}, headers={"Retry-After": "45"}), clock=clock)
    r = p.fetch_flow(*BBOX)
    assert r.status == "rate_limited" and "45s" in r.message and len(c.calls) == 1
    assert p.fetch_flow(*BBOX).status == "rate_limited" and len(c.calls) == 1      # no hammering while paused
    assert p.status()["state"] == "rate_limited"
    clock.t += 46
    p.client.responses = [Resp(200, flow_body())]
    assert p.fetch_flow(*BBOX).data_state == "live" and len(c.calls) == 2


def test_timeout_and_network_error():
    r = make(httpx.TimeoutException("slow"))[0].fetch_flow(*BBOX)
    assert (r.status, r.is_live) == ("timeout", False) and "timed out" in r.message
    r = make(httpx.ConnectError("dns"))[0].fetch_flow(*BBOX)
    assert (r.status, r.is_live, r.data_state) == ("error", False, "unavailable") and "ConnectError" in r.message


def test_http_400_and_500_are_errors():
    assert make(Resp(400, {"title": "Invalid BBox size"}))[0].fetch_flow(*BBOX).status == "error"
    assert make(Resp(503, {}))[0].fetch_flow(*BBOX).status == "error"


def test_invalid_and_oversized_bbox_rejected_before_any_request():
    p, c = make(Resp(200, flow_body()))
    for bad in ((85.9, 20.3, 85.8, 20.4), (85.0, 20.0, 87.0, 21.0), (200, 0, 201, 1)):
        with pytest.raises(ValueError):
            p.fetch_flow(*bad)
    assert not c.calls


# ------------------------------------------------------------------ route ETA
def eta_body(dur=1500, base=900, length=12000):
    return {"routes": [{"sections": [{"summary": {"duration": dur, "baseDuration": base, "length": length}}]}]}


def test_route_eta_traffic_vs_free_flow():
    p, c = make(Resp(200, eta_body()))
    e = p.route_eta((20.30, 85.82), (20.25, 85.78))
    pub = e.to_public()
    assert (e.status, e.data_state, e.is_live) == ("ok", "live", True)
    assert pub["duration_min"] == 25.0 and pub["free_flow_duration_min"] == 15.0 and pub["traffic_delay_min"] == 10.0 and pub["reference_only"]
    url, params = c.calls[0]
    assert "router.hereapi.com/v8/routes" in url and params["transportMode"] == "car" and params["return"] == "summary"
    assert "departureTime" not in params           # default = now, so duration includes live traffic
    assert p.route_eta((20.30, 85.82), (20.25, 85.78)).data_state == "cached" and len(c.calls) == 1


def test_route_eta_failures():
    assert make(Resp(200, {"routes": []}))[0].route_eta((1, 1), (2, 2)).status == "no_data"
    assert make(Resp(429, {}))[0].route_eta((1, 1), (2, 2)).status == "rate_limited"
    assert make(httpx.TimeoutException("x"))[0].route_eta((1, 1), (2, 2)).status == "timeout"


# ------------------------------------------------------------------ tile proxy
def test_tile_live_cached_stale_and_range():
    clock = Clock(); p, c = make(Resp(200, None, content=b"\x89PNG"), Resp(500, {}), clock=clock, tile_ttl=100, stale_max=600)
    t = p.tile(12, 3000, 1800)
    assert (t.status, t.data_state, t.content) == ("ok", "live", b"\x89PNG")
    assert "traffic.maps.hereapi.com/v3/flow/mc/12/3000/1800/png8" in c.calls[0][0] and c.calls[0][1]["apiKey"] == KEY
    assert p.tile(12, 3000, 1800).data_state == "cached" and len(c.calls) == 1
    clock.t += 200
    assert p.tile(12, 3000, 1800).data_state == "stale_cache"
    clock.t += 1000
    t = p.tile(12, 3000, 1800); assert (t.data_state, t.content) == ("unavailable", None)
    assert p.tile(3, 99, 0).status == "error"


# ------------------------------------------------------------------ factory
def test_factory_selection(monkeypatch):
    monkeypatch.setenv("HERE_API_KEY", "k")
    assert create_provider("here").name == "here"
    assert create_provider("TomTom").name == "tomtom"
    assert create_provider("none").name == "none" and not create_provider("none").configured
    unk = create_provider("bing"); assert unk.name == "none" and "Unknown TRAFFIC_PROVIDER" in unk.status()["message"]
    monkeypatch.setenv("TRAFFIC_PROVIDER", "tomtom"); assert create_provider().name == "tomtom"
    monkeypatch.delenv("TRAFFIC_PROVIDER"); assert create_provider().name == "here"


def test_none_provider_never_fabricates():
    r = create_provider("none").fetch_flow(*BBOX)
    assert (r.status, r.data_state, r.is_live, r.segments) == ("disabled", "disabled", False, [])
