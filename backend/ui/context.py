"""Shared, cached access to services and derived data for all dashboard pages."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from backend.dependencies import (  # noqa: E402
    ROOT, get_copilot, get_forecasts, get_incidents, get_optimizer, get_provider,
    get_tomtom, get_traffic, get_weather,
)
from simulation.traffic_simulator import TrafficSimulator  # noqa: E402
from ui import maps  # noqa: E402
from ui.status import IST, load_json  # noqa: E402

CENTER = (20.2961, 85.8245)


@dataclass
class Ctx:
    traffic: Any
    forecasts: Any
    weather: Any
    incidents: Any
    provider: Any
    tomtom: Any
    graph: Any
    edges: pd.DataFrame
    graph_key: str


def graph_key(provider) -> str:
    g = provider.graph
    return f"{provider.source}|{provider.loaded_at}|{g.number_of_nodes()}|{g.number_of_edges()}"


@st.cache_resource(show_spinner=False)
def _edges_cached(key: str, _graph) -> pd.DataFrame:
    return maps.edges_frame(_graph)


def build_ctx() -> Ctx:
    provider = get_provider()
    graph = provider.load_cached_only()  # never blocks on the network at start-up
    key = graph_key(provider)
    return Ctx(get_traffic(), get_forecasts(), get_weather(), get_incidents(), provider, get_tomtom(),
               graph, _edges_cached(key, graph), key)


@st.cache_data(show_spinner=False)
def location_anchors() -> dict[str, dict]:
    path = ROOT / "config" / "location_coordinates.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


@st.cache_data(ttl=300, show_spinner=False)
def predictions_table(_traffic, data_marker: str) -> pd.DataFrame:
    """Next-30-minute class + probabilities for every monitored location (cached 5 min)."""
    rows = []
    anchors = location_anchors()
    for loc in _traffic.list_locations():
        try:
            p = _traffic.predict_next(loc)
        except Exception as exc:  # keep the table usable if one location fails
            rows.append({"location": loc, "error": str(exc)}); continue
        probs = p.get("class_probabilities") or {}
        a = anchors.get(loc, {})
        rows.append({
            "location": loc, "predicted": p["predicted_congestion"],
            "confidence": max(probs.values()) if probs else None,
            "P(Low)": probs.get("Low"), "P(Medium)": probs.get("Medium"), "P(High)": probs.get("High"),
            "origin_timestamp": p["origin_timestamp"], "prediction_timestamp": p["prediction_timestamp"],
            "model": p.get("model_name"), "lat": a.get("lat"), "lon": a.get("lon"), "area": a.get("area", ""),
        })
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def simulated_congestion(key: str, hour_iso: str, rain_mm: float, _graph) -> dict:
    when = datetime.fromisoformat(hour_iso)
    return TrafficSimulator().edge_conditions(_graph, when, rainfall_mm=rain_mm)


@st.cache_data(ttl=600, show_spinner=False)
def weather_cached(_service, force: int = 0) -> dict:
    return _service.get_current(allow_simulated_fallback=False, force_refresh=bool(force))


@st.cache_data(ttl=60, show_spinner=False)
def eval_json(path: str):
    return load_json(Path(path))


def now_ist() -> datetime:
    return datetime.now(IST)


def hour_bucket(dt: datetime) -> str:
    return dt.replace(minute=0, second=0, microsecond=0, tzinfo=None).isoformat()
