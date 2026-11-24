"""Pluggable traffic providers. Select with ``TRAFFIC_PROVIDER=here|tomtom|none`` in backend/.env."""
from __future__ import annotations

import os
from typing import Any

from .base import (BHUBANESWAR_BBOX, CACHED, DISABLED, LIVE, STALE, UNAVAILABLE, FlowResult, FlowSegment, RouteEta,
                   TileResult, TrafficProvider)

PROVIDER_NAMES = ("here", "tomtom", "none")


class NullProvider(TrafficProvider):
    """Used for TRAFFIC_PROVIDER=none or an unknown value. Never fabricates data."""
    name = "none"

    def __init__(self, reason: str = "No external traffic provider selected (TRAFFIC_PROVIDER=none)."):
        self.reason = reason

    @property
    def configured(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": False, "state": "disabled", "message": self.reason,
                "coverage_observed": False, "coverage_note": "No provider selected.", "supports": {"flow": False, "tiles": False, "route_eta": False}}

    def fetch_flow(self, min_lon, min_lat, max_lon, max_lat) -> FlowResult:
        return FlowResult(self.name, "disabled", DISABLED, message=self.reason, bbox=(min_lon, min_lat, max_lon, max_lat))


def create_provider(name: str | None = None) -> TrafficProvider:
    """Factory. Default (TRAFFIC_PROVIDER unset) is 'here'; an unconfigured provider reports 'disabled', never fake data."""
    name = (name if name is not None else os.getenv("TRAFFIC_PROVIDER", "here")).strip().lower() or "here"
    if name == "here":
        from .here_provider import HereProvider
        return HereProvider()
    if name == "tomtom":
        from .tomtom_provider import TomTomProvider
        return TomTomProvider()
    if name == "none":
        return NullProvider()
    return NullProvider(f"Unknown TRAFFIC_PROVIDER '{name}'. Use one of: {', '.join(PROVIDER_NAMES)}.")
