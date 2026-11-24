"""Single place that decides which provenance badge each data source gets.

Rules (never relaxed): only a successful external response flagged ``is_live`` is
LIVE DATA; bundled/replayed/simulated values are SYNTHETIC DATA; stale or on-disk
results are CACHED DATA; everything else is UNAVAILABLE.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
LIVE, SYNTH, CACHED, UNAVAIL, OSM = "LIVE DATA", "SYNTHETIC DATA", "CACHED DATA", "UNAVAILABLE", "OSM · STATIC"
SIMULATED = "SIMULATED"


def payload_badge(payload: dict | None) -> str:
    """Badge for an adapter payload (weather, TomTom...)."""
    if not payload:
        return UNAVAIL
    status = str(payload.get("status", "")).casefold()
    if status in {"unavailable", "disabled", "error", "not_requested"}:
        return UNAVAIL
    if status == "simulated_fallback" or payload.get("is_simulated"):
        return SYNTH
    if status == "stale_cache" or payload.get("cache_status") in {"stale_cache", "fresh_cache"}:
        return CACHED
    if payload.get("is_live") and status == "ok":
        return LIVE
    return CACHED if status == "ok" else UNAVAIL


def road_badge(source: str) -> str:
    if source.startswith("openstreetmap"):
        return OSM
    if source == "synthetic_fallback":
        return SYNTH
    return UNAVAIL


def data_age_text(ts: pd.Timestamp | str, now: datetime | None = None) -> str:
    now = now or datetime.now(IST).replace(tzinfo=None)
    delta = now - pd.Timestamp(ts).to_pydatetime().replace(tzinfo=None)
    days = delta.days
    if days >= 365:
        return f"{days / 365.25:.1f} years old"
    if days >= 1:
        return f"{days} days old"
    return f"{delta.seconds // 3600} h old"


def congestion_counts(rows: list[dict], key: str = "congestion_label") -> dict[str, int]:
    out = {"Low": 0, "Medium": 0, "High": 0}
    for r in rows:
        label = r.get(key)
        if label in out:
            out[label] += 1
    return out


def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None
