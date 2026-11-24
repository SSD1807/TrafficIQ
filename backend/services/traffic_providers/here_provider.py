"""HERE Technologies traffic provider (Traffic API v7 flow, Routing API v8, Traffic Raster Tile API v3).

The API key is read ONLY from the backend environment (HERE_API_KEY) and is sent server-side; the browser talks to
this backend (tile proxy, flow endpoint) and never sees the key.

ASSUMPTIONS taken from HERE's public documentation and NOT yet confirmed against a live key in this project --
run ``python scripts/live_test_here.py`` to confirm them (it prints what was actually returned):
  * Flow speeds (speed / speedUncapped / freeFlow) are metres per second.
  * Shape points inside a flow item are ordered in the direction of travel.
  * Bhubaneswar is inside HERE's India traffic coverage. Nothing here claims that; ``status()['coverage_observed']`` only
    becomes true after a flow request for the Bhubaneswar window actually returned segments in this process.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from routing.flow_matching import FlowMeasurement
from .base import (BHUBANESWAR_BBOX, CACHED, DISABLED, LIVE, STALE, UNAVAILABLE, FlowResult, FlowSegment, RouteEta,
                   TileResult, TrafficProvider, TTLCache, validate_bbox)

FLOW_URL = "https://data.traffic.hereapi.com/v7/flow"
ROUTER_URL = "https://router.hereapi.com/v8/routes"
TILE_URL = "https://traffic.maps.hereapi.com/v3/flow/mc/{z}/{x}/{y}/png8"
_PLACEHOLDERS = {"", "your_key_here", "your_here_api_key", "changeme"}


def jam_level(jam_factor: float | None, closed: bool = False) -> str:
    """App-defined display buckets for HERE's 0-10 jamFactor (thresholds are ours, not HERE's)."""
    if closed:
        return "closed"
    if jam_factor is None:
        return "free"
    return "free" if jam_factor < 2.5 else "slow" if jam_factor < 5 else "heavy" if jam_factor < 8 else "severe"


def _clean_key(key: str | None) -> str | None:
    key = (key or "").strip().strip('"').strip("'")
    return None if key.lower() in _PLACEHOLDERS else key


class HereProvider(TrafficProvider):
    name = "here"
    supports_tiles = True
    supports_route_eta = True

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None, timeout_seconds: float = 8.0,
                 flow_ttl: float = 120.0, stale_max: float = 900.0, eta_ttl: float = 60.0, tile_ttl: float = 300.0,
                 clock=time.time):
        self.api_key = _clean_key(api_key if api_key is not None else os.getenv("HERE_API_KEY"))
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.timeout_seconds, self.flow_ttl, self.stale_max, self.eta_ttl, self.tile_ttl = timeout_seconds, flow_ttl, stale_max, eta_ttl, tile_ttl
        self.clock = clock
        self._flow_cache, self._eta_cache, self._tile_cache = TTLCache(64, clock), TTLCache(64, clock), TTLCache(512, clock)
        self._blocked_until = 0.0           # set after HTTP 429 so we stop hammering the API
        self._last_error: str | None = None
        self._last_success_at: float | None = None
        self._coverage_observed = False
        self._last_segment_count: int | None = None

    # ------------------------------------------------------------------ interface
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        now = self.clock()
        if not self.configured:
            state, msg = "disabled", "HERE_API_KEY is not configured in backend/.env."
        elif now < self._blocked_until:
            state, msg = "rate_limited", f"HERE rate limit hit; requests paused for {self._blocked_until - now:.0f}s."
        elif self._last_success_at is None:
            state, msg = "configured_not_tested", "Key present but no successful HERE request yet in this process."
        else:
            state, msg = "ok", "Last HERE request succeeded."
        return {"provider": self.name, "configured": self.configured, "state": state, "message": msg,
                "last_success_at": self._last_success_at, "last_error": self._last_error,
                "coverage_observed": self._coverage_observed, "last_segment_count": self._last_segment_count,
                "coverage_note": ("Bhubaneswar flow segments were returned by HERE in this process." if self._coverage_observed else
                                  "Bhubaneswar live coverage is NOT confirmed until a real HERE flow request returns segments."),
                "supports": {"flow": True, "tiles": True, "route_eta": True}}

    # ------------------------------------------------------------------ HTTP core
    def _request(self, url: str, params: dict[str, Any]) -> tuple[str, Any, str | None]:
        """-> (status, json|bytes|None, message). status: ok|disabled|unauthorized|rate_limited|timeout|no_data|error."""
        if not self.configured:
            return "disabled", None, "HERE_API_KEY is not configured in backend/.env."
        now = self.clock()
        if now < self._blocked_until:
            return "rate_limited", None, f"HERE rate limit hit; retry in {self._blocked_until - now:.0f}s."
        try:
            resp = self.client.get(url, params={**params, "apiKey": self.api_key}, timeout=self.timeout_seconds)
        except httpx.TimeoutException:
            return self._fail("timeout", f"HERE request timed out after {self.timeout_seconds:.0f}s.")
        except httpx.HTTPError as exc:
            return self._fail("error", f"HERE network error: {type(exc).__name__}")
        code = resp.status_code
        if code in (401, 403):
            return self._fail("unauthorized", f"HERE rejected the API key (HTTP {code}). Check the key and that the project has the Traffic/Routing services enabled.")
        if code == 429:
            try:
                wait = min(300.0, max(1.0, float(resp.headers.get("Retry-After", 60))))
            except (TypeError, ValueError):
                wait = 60.0
            self._blocked_until = self.clock() + wait
            return self._fail("rate_limited", f"HERE rate limit / quota reached (HTTP 429); pausing {wait:.0f}s.")
        if code == 400:
            return self._fail("error", f"HERE rejected the request (HTTP 400): {self._brief(resp)}")
        if code >= 400:
            return self._fail("error", f"HERE returned HTTP {code}.")
        self._last_success_at, self._last_error = self.clock(), None
        return "ok", resp, None

    def _fail(self, status: str, message: str):
        self._last_error = message
        return status, None, message

    @staticmethod
    def _brief(resp) -> str:
        try:
            body = resp.json()
            return str(body.get("title") or body.get("error_description") or body)[:160]
        except Exception:
            return "unreadable error body"

    # ------------------------------------------------------------------ flow
    def fetch_flow(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> FlowResult:
        bbox = (min_lon, min_lat, max_lon, max_lat)
        validate_bbox(*bbox)
        key = tuple(round(v, 3) for v in bbox)
        cached = self._flow_cache.get(key)
        if cached and cached[1] <= self.flow_ttl:
            r: FlowResult = cached[0]
            return self._relabel(r, CACHED, age=cached[1])
        status, resp, msg = self._request(FLOW_URL, {"in": "bbox:" + ",".join(f"{v:.6f}" for v in bbox), "locationReferencing": "shape"})
        if status != "ok":
            if cached and cached[1] <= self.stale_max:
                out = self._relabel(cached[0], STALE, age=cached[1])
                out.message = f"Live request failed ({msg}) - showing an older cached response; NOT live."
                return out
            return FlowResult(self.name, status, DISABLED if status == "disabled" else UNAVAILABLE, message=msg, bbox=bbox)
        try:
            result = self._parse_flow(resp.json(), bbox)
        except (ValueError, KeyError, TypeError, AttributeError):
            self._last_error = "HERE flow response could not be parsed."
            return FlowResult(self.name, "error", UNAVAILABLE, message=self._last_error, bbox=bbox)
        self._last_segment_count = len(result.segments)
        if result.segments and self._overlaps_bhubaneswar(bbox):
            self._coverage_observed = True
        if result.segments:
            self._flow_cache.set(key, result)
        return result

    @staticmethod
    def _overlaps_bhubaneswar(bbox) -> bool:
        b = BHUBANESWAR_BBOX
        return bbox[0] < b[2] and bbox[2] > b[0] and bbox[1] < b[3] and bbox[3] > b[1]

    def _relabel(self, r: FlowResult, state: str, age: float) -> FlowResult:
        return FlowResult(r.provider, r.status, state, is_live=False, message=r.message if state == CACHED else None, bbox=r.bbox,
                          fetched_at=r.fetched_at, age_seconds=age, source_updated=r.source_updated, segments=r.segments,
                          measurements=r.measurements, stats=dict(r.stats))

    def _parse_flow(self, body: dict[str, Any], bbox) -> FlowResult:
        now = self.clock()
        items = body.get("results")
        if not isinstance(items, list):
            raise ValueError("no results array")
        segments: list[FlowSegment] = []; measurements: list[FlowMeasurement] = []
        skipped = 0
        for item in items:
            flow = item.get("currentFlow") or {}
            loc = item.get("location") or {}
            links = (loc.get("shape") or {}).get("links") or []
            speed, free, jam = flow.get("speed"), flow.get("freeFlow"), flow.get("jamFactor")
            closed = str(flow.get("traversability", "")).lower() == "closed"
            conf = flow.get("confidence")
            if (speed is None and not closed) or free is None or not links:
                skipped += 1; continue
            cur_kph = 0.0 if closed and speed is None else float(speed) * 3.6
            free_kph = float(free) * 3.6
            for link in links:
                pts = [(float(p["lat"]), float(p["lng"])) for p in link.get("points", [])]
                if len(pts) < 2:
                    continue
                segments.append(FlowSegment(pts, round(cur_kph, 1), round(free_kph, 1), None if jam is None else float(jam),
                                            jam_level(None if jam is None else float(jam), closed),
                                            None if conf is None else float(conf), loc.get("description")))
                if conf is not None:  # a measurement without a provider confidence is displayed but never used for costing
                    measurements.append(FlowMeasurement(
                        query_lat=pts[0][0], query_lon=pts[0][1], current_speed_kph=cur_kph, free_flow_speed_kph=free_kph,
                        confidence=float(conf), road_closure=closed, coordinates=pts, frc=None, is_live=True, fetched_at=now))
        status = "ok" if segments else "no_data"
        msg = None if segments else "HERE returned no flow segments for this area (no coverage or no data)."
        return FlowResult(self.name, status, LIVE if segments else UNAVAILABLE, is_live=bool(segments), message=msg, bbox=bbox,
                          fetched_at=now, age_seconds=0.0, source_updated=body.get("sourceUpdated"), segments=segments,
                          measurements=measurements, stats={"items_skipped": skipped})

    # ------------------------------------------------------------------ route ETA (traffic vs free-flow)
    def route_eta(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteEta:
        key = (round(origin[0], 4), round(origin[1], 4), round(destination[0], 4), round(destination[1], 4))
        cached = self._eta_cache.get(key)
        if cached and cached[1] <= self.eta_ttl:
            e: RouteEta = cached[0]
            return RouteEta(e.provider, e.status, CACHED, False, None, e.duration_s, e.base_duration_s, e.length_m, cached[1])
        status, resp, msg = self._request(ROUTER_URL, {"transportMode": "car", "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
                                                         "destination": f"{destination[0]:.6f},{destination[1]:.6f}", "return": "summary"})
        if status != "ok":
            return RouteEta(self.name, status, DISABLED if status == "disabled" else UNAVAILABLE, message=msg)
        try:
            sections = resp.json()["routes"][0]["sections"]
            dur = sum(float(s["summary"]["duration"]) for s in sections)
            base = sum(float(s["summary"]["baseDuration"]) for s in sections)
            length = sum(float(s["summary"]["length"]) for s in sections)
        except (KeyError, IndexError, TypeError, ValueError):
            return RouteEta(self.name, "no_data", UNAVAILABLE, message="HERE returned no route for these points.")
        eta = RouteEta(self.name, "ok", LIVE, True, None, dur, base, length, 0.0)
        self._eta_cache.set(key, eta)
        return eta

    # ------------------------------------------------------------------ raster tiles (server-side proxy)
    def tile(self, z: int, x: int, y: int) -> TileResult:
        if not (0 <= z <= 20 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            return TileResult("error", UNAVAILABLE, message="Tile coordinates out of range.")
        key = (z, x, y)
        cached = self._tile_cache.get(key)
        if cached and cached[1] <= self.tile_ttl:
            return TileResult("ok", CACHED, cached[0], age_seconds=cached[1])
        status, resp, msg = self._request(TILE_URL.format(z=z, x=x, y=y), {"size": 256})
        if status != "ok":
            if cached and cached[1] <= self.stale_max:
                return TileResult("ok", STALE, cached[0], message="Live tile request failed; older cached tile.", age_seconds=cached[1])
            return TileResult(status, DISABLED if status == "disabled" else UNAVAILABLE, message=msg)
        self._tile_cache.set(key, resp.content)
        return TileResult("ok", LIVE, resp.content, age_seconds=0.0)
