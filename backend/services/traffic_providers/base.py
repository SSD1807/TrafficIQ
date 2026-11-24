"""Provider-neutral traffic types and the TrafficProvider interface.

Data-state vocabulary (the ONLY labels the UI/API may show for traffic data):
  live         fetched from the provider during this request
  cached       fetched from the provider earlier and still inside the fresh-cache TTL (age is reported)
  stale_cache  provider failed; an older cached response is served, explicitly NOT live
  unavailable  no usable provider data (missing key, auth/rate-limit/timeout/error, no data for the area)
  disabled     provider is not configured
Simulated traffic never comes from a provider; it is produced by simulation/traffic_simulator.py and labelled there.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from routing.flow_matching import FlowMeasurement

LIVE, CACHED, STALE, UNAVAILABLE, DISABLED = "live", "cached", "stale_cache", "unavailable", "disabled"

# Bhubaneswar map window used for defaults and for the "coverage observed" check.
BHUBANESWAR_BBOX = (85.74, 20.22, 85.92, 20.37)  # min_lon, min_lat, max_lon, max_lat


def validate_bbox(min_lon: float, min_lat: float, max_lon: float, max_lat: float, max_span_deg: float = 1.0) -> None:
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise ValueError("Invalid bounding box (expected min_lon < max_lon and min_lat < max_lat inside WGS84 range)")
    if (max_lon - min_lon) > max_span_deg or (max_lat - min_lat) > max_span_deg:
        raise ValueError(f"Bounding box too large (max {max_span_deg} degree per side)")


class TTLCache:
    """Tiny in-memory cache that reports entry age so callers can label cached vs stale data."""

    def __init__(self, max_items: int = 256, clock=time.time):
        self._d: dict[Any, tuple[float, Any]] = {}
        self.max_items, self.clock = max_items, clock

    def get(self, key) -> tuple[Any, float] | None:
        hit = self._d.get(key)
        return None if hit is None else (hit[1], self.clock() - hit[0])

    def set(self, key, value) -> None:
        if len(self._d) >= self.max_items and key not in self._d:
            self._d.pop(min(self._d, key=lambda k: self._d[k][0]))
        self._d[key] = (self.clock(), value)


@dataclass
class FlowSegment:
    path: list[tuple[float, float]]          # (lat, lon) in direction of travel
    current_kph: float | None
    free_flow_kph: float | None
    jam_factor: float | None                 # provider scale 0 (free) .. 10 (closed)
    level: str                               # free | slow | heavy | severe | closed  (app-defined thresholds)
    confidence: float | None = None
    description: str | None = None


@dataclass
class FlowResult:
    provider: str
    status: str                              # ok | disabled | unauthorized | rate_limited | timeout | no_data | error
    data_state: str                          # live | cached | stale_cache | unavailable | disabled
    is_live: bool = False
    message: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    fetched_at: float | None = None          # epoch seconds of the provider fetch
    age_seconds: float | None = None
    source_updated: str | None = None        # provider-reported data timestamp, if any
    segments: list[FlowSegment] = field(default_factory=list)
    measurements: list[FlowMeasurement] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status == "ok" and bool(self.measurements)

    def to_public(self, include_segments: bool = True) -> dict[str, Any]:
        out = {"provider": self.provider, "status": self.status, "data_state": self.data_state, "is_live": self.is_live,
               "message": self.message, "bbox": list(self.bbox) if self.bbox else None, "fetched_at": self.fetched_at,
               "age_seconds": None if self.age_seconds is None else round(self.age_seconds, 1),
               "source_updated": self.source_updated, "segment_count": len(self.segments),
               "measurement_count": len(self.measurements), **self.stats}
        if include_segments:
            out["segments"] = [{"path": [[round(a, 6), round(b, 6)] for a, b in s.path], "current_kph": s.current_kph,
                                "free_flow_kph": s.free_flow_kph, "jam_factor": s.jam_factor, "level": s.level,
                                "confidence": s.confidence, "description": s.description} for s in self.segments]
        return out


@dataclass
class RouteEta:
    provider: str
    status: str
    data_state: str
    is_live: bool = False
    message: str | None = None
    duration_s: float | None = None          # with traffic
    base_duration_s: float | None = None     # free-flow, no traffic
    length_m: float | None = None
    age_seconds: float | None = None

    def to_public(self) -> dict[str, Any]:
        delay = None if self.duration_s is None or self.base_duration_s is None else max(0.0, self.duration_s - self.base_duration_s)
        return {"provider": self.provider, "status": self.status, "data_state": self.data_state, "is_live": self.is_live,
                "message": self.message, "duration_min": None if self.duration_s is None else round(self.duration_s / 60, 2),
                "free_flow_duration_min": None if self.base_duration_s is None else round(self.base_duration_s / 60, 2),
                "traffic_delay_min": None if delay is None else round(delay / 60, 2),
                "length_km": None if self.length_m is None else round(self.length_m / 1000, 3),
                "age_seconds": None if self.age_seconds is None else round(self.age_seconds, 1),
                "reference_only": True,
                "note": "Provider ETA on the provider's own road network; shown for comparison, it is not the OSM route time."}


@dataclass
class TileResult:
    status: str
    data_state: str
    content: bytes | None = None
    content_type: str = "image/png"
    message: str | None = None
    age_seconds: float | None = None


class TrafficProvider(ABC):
    name: str = "base"
    supports_tiles: bool = False
    supports_route_eta: bool = False

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Configuration/runtime state only. Must not call the external API."""

    @abstractmethod
    def fetch_flow(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> FlowResult: ...

    def corridor_flow(self, routes: list[dict], bbox: tuple[float, float, float, float]) -> FlowResult:
        """Flow for the corridor of candidate routes. Default: one area request for the corridor bbox."""
        return self.fetch_flow(*bbox)

    def route_eta(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteEta:
        return RouteEta(self.name, "unsupported", "unavailable", message=f"{self.name} does not provide route ETAs in this app.")

    def tile(self, z: int, x: int, y: int) -> TileResult:
        return TileResult("unsupported", "unavailable", message=f"{self.name} does not provide a traffic tile layer in this app.")
