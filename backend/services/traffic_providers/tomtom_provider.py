"""Optional TomTom provider: wraps the EXISTING services/tomtom_service.py and routing/tomtom_routing.py unchanged.

TomTom flow is point-based, so ``fetch_flow`` probes a small grid and ``corridor_flow`` reuses the existing corridor
probe. TomTom has never been verified live in this project (no key was ever supplied).
"""
from __future__ import annotations

from typing import Any

from routing.flow_matching import parse_flow_payload
from routing.tomtom_routing import probe_corridor
from services.tomtom_service import TomTomService
from .base import CACHED, DISABLED, LIVE, UNAVAILABLE, FlowResult, FlowSegment, TrafficProvider, validate_bbox
from .here_provider import jam_level


class TomTomProvider(TrafficProvider):
    name = "tomtom"

    def __init__(self, service: TomTomService | None = None, grid: int = 3, max_calls: int = 24):
        self.service = service or TomTomService()
        self.grid, self.max_calls = grid, max_calls

    @property
    def configured(self) -> bool:
        return bool(self.service.api_key)

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": self.configured,
                "state": "configured_not_tested" if self.configured else "disabled",
                "message": "TOMTOM_API_KEY present; never verified live in this project." if self.configured else "TOMTOM_API_KEY is not configured in backend/.env.",
                "coverage_observed": False, "coverage_note": "TomTom coverage of Bhubaneswar has not been tested here.",
                "supports": {"flow": True, "tiles": False, "route_eta": False}}

    def _result(self, measurements, stats, bbox) -> FlowResult:
        if not self.configured:
            return FlowResult(self.name, "disabled", DISABLED, message="TOMTOM_API_KEY is not configured in backend/.env.", bbox=bbox)
        if not measurements:
            return FlowResult(self.name, "no_data", UNAVAILABLE, message="TomTom returned no usable flow data.", bbox=bbox, stats=stats)
        segs = []
        for m in measurements:
            jam = None if m.current_speed_kph <= 0 else max(0.0, min(10.0, (1 - m.current_speed_kph / max(m.free_flow_speed_kph, 1e-6)) * 10))
            segs.append(FlowSegment(m.coordinates, round(m.current_speed_kph, 1), round(m.free_flow_speed_kph, 1), jam,
                                    jam_level(jam, m.road_closure), m.confidence))
        import time
        fetched = min((m.fetched_at for m in measurements if m.fetched_at), default=None)
        age = None if fetched is None else max(0.0, time.time() - fetched)
        cached = age is not None and age > 5.0  # TomTomService caches payloads for up to 180 s; label older ones honestly
        return FlowResult(self.name, "ok", CACHED if cached else LIVE, is_live=not cached, bbox=bbox, fetched_at=fetched,
                          age_seconds=age, segments=segs, measurements=measurements, stats=stats)

    def fetch_flow(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> FlowResult:
        bbox = (min_lon, min_lat, max_lon, max_lat); validate_bbox(*bbox)
        n = max(1, min(self.grid, 5)); out = []; failed = 0
        for i in range(n):
            for j in range(n):
                lat = min_lat + (i + 0.5) * (max_lat - min_lat) / n; lon = min_lon + (j + 0.5) * (max_lon - min_lon) / n
                if len(out) + failed >= self.max_calls:
                    break
                m = parse_flow_payload(self.service.flow_at(lat, lon), lat, lon) if self.configured else None
                out.append(m) if m else None; failed += 0 if m else 1
        return self._result(out, {"probes_failed": failed}, bbox)

    def corridor_flow(self, routes: list[dict], bbox) -> FlowResult:
        if not self.configured:
            return self._result([], {}, bbox)
        measurements, stats = probe_corridor(self.service, routes, max_calls=self.max_calls)
        return self._result(measurements, stats, bbox)
