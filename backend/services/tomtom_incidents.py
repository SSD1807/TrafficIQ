"""TomTom Traffic *Incident Details* (v5) client.

Independent of services/tomtom_service.py (Flow Segment Data): either integration can fail or be disabled without
affecting the other.

Endpoint and response structure follow the official documentation
(https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-incidents/incident-details):

    GET https://api.tomtom.com/traffic/services/5/incidentDetails?key=..&bbox=minLon,minLat,maxLon,maxLat
        &fields={incidents{type,geometry{type,coordinates},properties{...}}}&language=..&timeValidityFilter=present

* ``bbox`` is lower-left / upper-right in EPSG:4326, maximum area 10,000 km2.
* Geometry is a GeoJSON Point or LineString; coordinates are [longitude, latitude].
* iconCategory 0..11,14; magnitudeOfDelay 0 unknown, 1 minor, 2 moderate, 3 major, 4 undefined (closures).
* Documented HTTP codes: 200, 400, 403, 405, 429, 500 (401 and other 5xx are handled defensively too).

Honesty rules: this module never fabricates incidents. "No incidents" is only reported after a successful, valid
HTTP 200 response whose ``incidents`` array is empty. A failed request keeps the previously fetched incidents (flagged
as cached) and is reported as an error, never as an empty feed. The API key is never logged or returned.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

log = logging.getLogger("trafficiq.tomtom_incidents")

BASE_URL = "https://api.tomtom.com"
INCIDENT_PATH = "/traffic/services/5/incidentDetails"
MAX_BBOX_AREA_KM2 = 10_000.0
DEFAULT_BBOX = (85.74, 20.22, 85.92, 20.37)  # Bhubaneswar: minLon, minLat, maxLon, maxLat

# Only documented fields (see "Value with all available fields" in the official docs); tmc/aci are not needed.
FIELDS = ("{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,"
          "events{description,code,iconCategory},startTime,endTime,from,to,length,delay,roadNumbers,"
          "timeValidity,probabilityOfOccurrence,numberOfReports,lastReportTime}}}")

ICON_CATEGORY = {0: "Unknown", 1: "Accident", 2: "Fog", 3: "Dangerous conditions", 4: "Rain", 5: "Ice", 6: "Jam",
                 7: "Lane closed", 8: "Road closed", 9: "Road works", 10: "Wind", 11: "Flooding", 14: "Broken down vehicle"}
# Provider category -> the app's internal incident kind (routing/incident_manager.VALID_KINDS).
KIND_BY_ICON = {1: "accident", 8: "road_closure", 9: "road_work", 7: "obstruction", 14: "obstruction", 6: "traffic_jam",
                0: "hazard", 2: "hazard", 3: "hazard", 4: "hazard", 5: "hazard", 10: "hazard", 11: "hazard"}
MAGNITUDE_LABEL = {0: "unknown", 1: "minor", 2: "moderate", 3: "major", 4: "undefined"}
# magnitudeOfDelay -> 0..1 severity used by the routing cost model. 0 (unknown) / 4 (undefined) give no provider severity.
SEVERITY_BY_MAGNITUDE = {1: 0.3, 2: 0.6, 3: 0.9}
DEFAULT_SEVERITY_BY_KIND = {"accident": 0.5, "road_work": 0.35, "obstruction": 0.4, "hazard": 0.3, "traffic_jam": 0.4,
                            "road_closure": 1.0}
CONFIRMED_PROBABILITIES = {"certain", "probable"}  # risk_of / improbable are shown but never applied to routing

STATE_LIVE = "live"
STATE_LIVE_EMPTY = "live_empty"
STATE_CACHED = "cached"
STATE_DISABLED_NO_KEY = "disabled_no_key"
STATE_DISABLED = "disabled"
STATE_UNAVAILABLE = "unavailable"
STATE_IDLE = "idle"


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        value = default
    return max(minimum, value) if minimum is not None else value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def bbox_area_km2(bbox: tuple[float, float, float, float]) -> float:
    min_lon, min_lat, max_lon, max_lat = bbox
    mid = math.radians((min_lat + max_lat) / 2)
    return abs(max_lon - min_lon) * 111.32 * math.cos(mid) * abs(max_lat - min_lat) * 110.54


def parse_bbox(text: str | None) -> tuple[float, float, float, float]:
    """Parse 'minLon,minLat,maxLon,maxLat'; raises ValueError for anything the API would reject."""
    if text is None or not str(text).strip():
        return DEFAULT_BBOX
    try:
        parts = tuple(float(p) for p in str(text).split(","))
    except ValueError as exc:
        raise ValueError("bbox must be four comma-separated numbers: minLon,minLat,maxLon,maxLat") from exc
    if len(parts) != 4:
        raise ValueError("bbox must be four comma-separated numbers: minLon,minLat,maxLon,maxLat")
    validate_bbox(parts)
    return parts  # type: ignore[return-value]


def validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise ValueError("Invalid bounding box: need minLon < maxLon, minLat < maxLat within valid coordinates")
    if bbox_area_km2(bbox) > MAX_BBOX_AREA_KM2:
        raise ValueError(f"Bounding box exceeds TomTom's maximum area of {MAX_BBOX_AREA_KM2:,.0f} km2")


@dataclass
class IncidentConfig:
    enabled: bool = True
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX
    poll_interval_seconds: float = 60.0
    min_refresh_seconds: float = 10.0
    timeout_seconds: float = 8.0
    stale_after_seconds: float = 900.0
    language: str = "en-GB"
    include_future: bool = False

    @classmethod
    def from_env(cls) -> "IncidentConfig":
        try:
            bbox = parse_bbox(os.getenv("TOMTOM_INCIDENTS_BBOX"))
        except ValueError as exc:
            log.warning("Ignoring invalid TOMTOM_INCIDENTS_BBOX (%s); using the Bhubaneswar default.", exc)
            bbox = DEFAULT_BBOX
        return cls(
            enabled=_env_bool("TOMTOM_INCIDENTS_ENABLED", True),
            bbox=bbox,
            poll_interval_seconds=_env_float("TOMTOM_INCIDENTS_POLL_INTERVAL", 60.0, minimum=30.0),
            min_refresh_seconds=_env_float("TOMTOM_INCIDENTS_MIN_REFRESH_SECONDS", 10.0, minimum=1.0),
            timeout_seconds=_env_float("TOMTOM_INCIDENTS_TIMEOUT", 8.0, minimum=1.0),
            stale_after_seconds=_env_float("TOMTOM_INCIDENTS_STALE_AFTER", 900.0, minimum=60.0),
            language=os.getenv("TOMTOM_INCIDENTS_LANGUAGE", "").strip() or "en-GB",
            include_future=_env_bool("TOMTOM_INCIDENTS_INCLUDE_FUTURE", False),
        )


# ---------------------------------------------------------------------------------------------------------------------
# Normalisation of one provider incident. Every optional field may be absent or null.
# ---------------------------------------------------------------------------------------------------------------------
def _parse_iso(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _int(value: Any) -> int | None:
    out = _num(value)
    return int(out) if out is not None else None


def _coord(pair: Any) -> list[float] | None:
    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
        return None
    lon, lat = _num(pair[0]), _num(pair[1])
    if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return [lon, lat]


def _geometry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    gtype, coords = raw.get("type"), raw.get("coordinates")
    if gtype == "Point":
        c = _coord(coords)
        return {"type": "Point", "coordinates": c} if c else None
    if gtype == "LineString" and isinstance(coords, list):
        pts = [_coord(p) for p in coords]
        if len(pts) >= 2 and all(p is not None for p in pts):
            return {"type": "LineString", "coordinates": pts}
    return None


def _line_length_m(coords: list[list[float]]) -> float:
    total = 0.0
    for (lo1, la1), (lo2, la2) in zip(coords[:-1], coords[1:]):
        total += math.hypot((lo2 - lo1) * 111_320 * math.cos(math.radians((la1 + la2) / 2)), (la2 - la1) * 110_540)
    return total


def representative_point(geometry: dict[str, Any]) -> tuple[float, float]:
    """(lat, lon) of a Point, or of the middle of a LineString measured along its length."""
    if geometry["type"] == "Point":
        lon, lat = geometry["coordinates"]
        return lat, lon
    coords, half, run = geometry["coordinates"], _line_length_m(geometry["coordinates"]) / 2, 0.0
    for a, b in zip(coords[:-1], coords[1:]):
        seg = _line_length_m([a, b])
        if run + seg >= half and seg > 0:
            t = (half - run) / seg
            return a[1] + t * (b[1] - a[1]), a[0] + t * (b[0] - a[0])
        run += seg
    return coords[-1][1], coords[-1][0]


def normalize_incident(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Convert one raw API incident into the app's provider-incident dict. Returns (incident, None) or (None, reason)."""
    if not isinstance(raw, dict):
        return None, "incident is not an object"
    props = raw.get("properties")
    if not isinstance(props, dict):
        return None, "missing properties"
    geometry = _geometry(raw.get("geometry"))
    if geometry is None:
        return None, "missing or invalid geometry"

    icon = _int(props.get("iconCategory"))
    events = [e for e in (props.get("events") or []) if isinstance(e, dict)] if isinstance(props.get("events"), list) else []
    if icon is None and events:
        icon = _int(events[0].get("iconCategory"))
    icon = 0 if icon is None else icon
    kind = KIND_BY_ICON.get(icon, "hazard")
    category = ICON_CATEGORY.get(icon, f"Category {icon}")

    descriptions: list[str] = []
    for e in events:
        d = e.get("description")
        if isinstance(d, str) and d.strip() and d.strip() not in descriptions:
            descriptions.append(d.strip())
    description = "; ".join(descriptions) or category

    magnitude = _int(props.get("magnitudeOfDelay"))
    if kind == "road_closure":
        severity, severity_source = 1.0, "provider_closure"
    elif magnitude in SEVERITY_BY_MAGNITUDE:
        severity, severity_source = SEVERITY_BY_MAGNITUDE[magnitude], "provider_magnitude"
    else:
        severity, severity_source = DEFAULT_SEVERITY_BY_KIND[kind], "default_for_kind"

    probability = props.get("probabilityOfOccurrence")
    probability = probability if isinstance(probability, str) and probability else None
    time_validity = props.get("timeValidity") if isinstance(props.get("timeValidity"), str) else None
    road_numbers = [str(r) for r in props["roadNumbers"] if r] if isinstance(props.get("roadNumbers"), list) else []

    provider_id = props.get("id")
    synthetic_id = not (isinstance(provider_id, str) and provider_id.strip())
    if synthetic_id:
        pts = geometry["coordinates"] if geometry["type"] == "LineString" else [geometry["coordinates"]]
        basis = f"{icon}|{props.get('startTime')}|{pts[0]}|{pts[-1]}"
        provider_id = "synthetic-" + hashlib.sha1(basis.encode()).hexdigest()[:24]
    else:
        provider_id = provider_id.strip()

    lat, lon = representative_point(geometry)
    return {
        "provider": "tomtom",
        "provider_id": provider_id,
        "provider_id_synthetic": synthetic_id,
        "kind": kind,
        "icon_category": icon,
        "category": category,
        "description": description,
        "severity": severity,
        "severity_source": severity_source,
        "magnitude_of_delay": magnitude,
        "magnitude_label": MAGNITUDE_LABEL.get(magnitude) if magnitude is not None else None,
        "start_time": _parse_iso(props.get("startTime")),
        "end_time": _parse_iso(props.get("endTime")),
        "from": props.get("from") if isinstance(props.get("from"), str) else None,
        "to": props.get("to") if isinstance(props.get("to"), str) else None,
        "length_m": _num(props.get("length")),
        "delay_seconds": _int(props.get("delay")),
        "road_numbers": road_numbers,
        "time_validity": time_validity,
        "probability_of_occurrence": probability,
        # A missing probability is treated as confirmed because we only request timeValidityFilter=present.
        "confirmed": probability is None or probability in CONFIRMED_PROBABILITIES,
        "number_of_reports": _int(props.get("numberOfReports")),
        "last_report_time": _parse_iso(props.get("lastReportTime")),
        "geometry": geometry,
        "latitude": lat,
        "longitude": lon,
    }, None


def redact(text: str, key: str | None) -> str:
    return text.replace(key, "***") if key else text


# ---------------------------------------------------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class FetchOutcome:
    """Result of one ``refresh()`` call."""
    fetched: bool                 # True only when this call completed a successful, valid API response
    throttled: bool               # True when no HTTP request was made (min interval / back-off / disabled)
    incidents: list[dict[str, Any]]
    status: dict[str, Any]


class TomTomIncidentService:
    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None,
                 config: IncidentConfig | None = None, clock: Callable[[], float] = time.time):
        self.api_key = (api_key if api_key is not None else os.getenv("TOMTOM_API_KEY", "")).strip()
        self.config = config or IncidentConfig.from_env()
        self.client = client or httpx.Client(timeout=self.config.timeout_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._incidents: list[dict[str, Any]] = []
        self._last_attempt_at: float | None = None
        self._last_success_at: float | None = None
        self._last_attempt_ok = False
        self._last_error: dict[str, Any] | None = None
        self._failures = 0
        self._backoff_until = 0.0
        self._skipped_invalid = 0
        self._traffic_model_id: str | None = None

    # ---- configuration state -------------------------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def active(self) -> bool:
        return self.enabled and self.configured

    # ---- public API ----------------------------------------------------------------------------------------------
    def current_incidents(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(i) for i in self._incidents]

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            cfg = self.config
            age = None if self._last_success_at is None else max(0.0, now - self._last_success_at)
            stale = age is not None and age > cfg.stale_after_seconds
            fresh_window = max(2 * cfg.poll_interval_seconds, 120.0)
            if not cfg.enabled:
                state, message = STATE_DISABLED, "Incident detection is disabled (TOMTOM_INCIDENTS_ENABLED=false)."
            elif not self.configured:
                state, message = STATE_DISABLED_NO_KEY, "TOMTOM_API_KEY is not configured in backend/.env; no live incidents are available."
            elif self._last_success_at is None:
                if self._last_error:
                    state, message = STATE_UNAVAILABLE, "TomTom incident service is temporarily unavailable: " + self._last_error["message"]
                else:
                    state, message = STATE_IDLE, "No incident request has been made yet."
            elif self._last_attempt_ok and age is not None and age <= fresh_window:
                state = STATE_LIVE if self._incidents else STATE_LIVE_EMPTY
                message = (f"Live: {len(self._incidents)} incident(s) reported by TomTom." if self._incidents
                           else "Live: TomTom returned no incidents for this area (a valid result).")
            else:
                state = STATE_CACHED
                message = ("Using cached TomTom incidents from the last successful fetch"
                           + (f"; latest attempt failed: {self._last_error['message']}" if self._last_error and not self._last_attempt_ok else "")
                           + (" (data is stale and is no longer applied to routing)." if stale else "."))
            next_allowed = max(self._backoff_until, (self._last_attempt_at or 0) + cfg.min_refresh_seconds)
            return {
                "provider": "tomtom", "feed": "traffic_incident_details_v5",
                "state": state, "message": message,
                "enabled": cfg.enabled, "configured": self.configured,
                "is_live": state in (STATE_LIVE, STATE_LIVE_EMPTY),
                "using_cached_data": state == STATE_CACHED, "stale": bool(stale),
                "incident_count": len(self._incidents),
                "last_attempt_at": _iso(self._last_attempt_at), "last_success_at": _iso(self._last_success_at),
                "data_age_seconds": None if age is None else round(age, 1),
                "last_error": dict(self._last_error) if self._last_error and not self._last_attempt_ok else None,
                "consecutive_failures": self._failures,
                "skipped_invalid_incidents": self._skipped_invalid,
                "poll_interval_seconds": cfg.poll_interval_seconds, "min_refresh_seconds": cfg.min_refresh_seconds,
                "stale_after_seconds": cfg.stale_after_seconds,
                "next_allowed_refresh_at": _iso(next_allowed) if next_allowed > now else None,
                "bbox": list(cfg.bbox), "bbox_area_km2": round(bbox_area_km2(cfg.bbox), 1),
                "traffic_model_id": self._traffic_model_id,
                "attribution": "Incident data: TomTom Traffic API (live reports; not synthetic).",
            }

    def refresh(self) -> FetchOutcome:
        """Fetch incidents (respecting disabled/no-key, min interval and back-off). Never raises for provider failures."""
        with self._lock:
            now = self._clock()
            if not self.active:
                return FetchOutcome(False, True, self.current_incidents(), self.status())
            if now < self._backoff_until or (self._last_attempt_at is not None and now - self._last_attempt_at < self.config.min_refresh_seconds):
                return FetchOutcome(False, True, self.current_incidents(), self.status())
            self._last_attempt_at = now
            try:
                incidents, skipped, model_id = self._request()
            except _FetchError as err:
                self._record_failure(err, now)
                return FetchOutcome(False, False, self.current_incidents(), self.status())
            self._incidents, self._skipped_invalid, self._traffic_model_id = incidents, skipped, model_id
            self._last_success_at, self._last_attempt_ok, self._last_error = now, True, None
            self._failures, self._backoff_until = 0, 0.0
            return FetchOutcome(True, False, self.current_incidents(), self.status())

    # ---- internals -----------------------------------------------------------------------------------------------
    def _params(self) -> dict[str, str]:
        min_lon, min_lat, max_lon, max_lat = self.config.bbox
        params = {"key": self.api_key, "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}", "fields": FIELDS,
                  "language": self.config.language,
                  "timeValidityFilter": "present,future" if self.config.include_future else "present"}
        return params

    def _request(self) -> tuple[list[dict[str, Any]], int, str | None]:
        try:
            response = self.client.get(BASE_URL + INCIDENT_PATH, params=self._params(),
                                       headers={"Accept-Encoding": "gzip"}, timeout=self.config.timeout_seconds)
        except httpx.TimeoutException:
            raise _FetchError("timeout", f"request timed out after {self.config.timeout_seconds:g}s")
        except httpx.RequestError as exc:  # exception text can embed the URL (and key), so only the class name is kept
            raise _FetchError("network", f"network error ({type(exc).__name__})")
        code = response.status_code
        if code in (401, 403):
            raise _FetchError("auth", f"TomTom rejected the API key (HTTP {code}); check TOMTOM_API_KEY and that the key has Traffic API access", code)
        if code == 429:
            raise _FetchError("rate_limited", "TomTom rate limit / quota reached (HTTP 429)", code, retry_after=_retry_after(response))
        if code == 400:
            raise _FetchError("bad_request", "TomTom rejected the request (HTTP 400): " + redact(_error_detail(response), self.api_key), code)
        if 500 <= code <= 599:
            raise _FetchError("server_error", f"TomTom server error (HTTP {code})", code)
        if code != 200:
            raise _FetchError("http_error", f"unexpected HTTP status {code}", code)
        try:
            body = response.json()
        except ValueError:
            raise _FetchError("invalid_response", "response was not valid JSON", code)
        if not isinstance(body, dict) or "detailedError" in body or not isinstance(body.get("incidents"), list):
            raise _FetchError("invalid_response", "response did not contain an 'incidents' array", code)
        raw_items = body["incidents"]
        seen: dict[str, dict[str, Any]] = {}
        skipped = 0
        for raw in raw_items:
            item, _reason = normalize_incident(raw)
            if item is None:
                skipped += 1
            else:
                seen.setdefault(item["provider_id"], item)  # de-duplicate repeated ids inside one response
        if raw_items and not seen:
            raise _FetchError("invalid_response", f"none of the {len(raw_items)} returned incidents could be parsed", code)
        return list(seen.values()), skipped, response.headers.get("TrafficModelID")

    def _record_failure(self, err: "_FetchError", now: float) -> None:
        self._failures += 1
        self._last_attempt_ok = False
        self._last_error = {"type": err.kind, "message": err.message, "http_status": err.status, "at": _iso(now)}
        wait = err.retry_after or min(self.config.poll_interval_seconds * (2 ** (self._failures - 1)), 600.0)
        if err.kind == "auth":
            wait = max(wait, 600.0)  # a bad key will not fix itself; do not hammer the API
        self._backoff_until = now + wait
        log.warning("TomTom incident fetch failed: %s (%s); retrying no sooner than %.0fs", err.kind, err.message, wait)


class _FetchError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.message, self.status, self.retry_after = kind, message, status, retry_after


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return min(max(value, 1.0), 3600.0)


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detailedError") or response.json().get("error")
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("code") or "bad request")[:200]
        return str(detail)[:200] if detail else "bad request"
    except Exception:
        return "bad request"
