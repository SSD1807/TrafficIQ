
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from routing.incident_manager import IncidentManager
from routing.osm_network import NetworkUnavailableError, OSMNetworkProvider
from routing.route_optimizer import RouteOptimizer
from services.traffic_service import TrafficService
from services.weather_service import WeatherService
from services.tomtom_service import TomTomService
from services.tomtom_incidents import TomTomIncidentService
from services.incident_sync import IncidentSyncService
from services.traffic_providers import create_provider
from forecasting.forecast_service import ForecastService
from copilot.agent import TrafficCopilot


@lru_cache(maxsize=1)
def get_incidents() -> IncidentManager:
    return IncidentManager()


@lru_cache(maxsize=1)
def get_provider() -> OSMNetworkProvider:
    return OSMNetworkProvider(
        ROOT / "cache" / "bhubaneswar_drive.graphml",
        place=os.getenv(
            "OSM_PLACE_QUERY",
            "Bhubaneswar, Khordha, Odisha, India"
        )
    )


@lru_cache(maxsize=1)
def get_traffic() -> TrafficService:
    return TrafficService(
        ROOT / "dataset" / "bhubaneswar_synthetic_traffic.csv",
        ROOT / "models" / "best_model.pkl"
    )


@lru_cache(maxsize=1)
def get_forecasts() -> ForecastService:
    return ForecastService(
        ROOT / "dataset" / "bhubaneswar_synthetic_traffic.csv",
        ROOT / "models" / "forecasting"
    )


@lru_cache(maxsize=1)
def get_weather() -> WeatherService:
    return WeatherService()


@lru_cache(maxsize=1)
def get_tomtom() -> TomTomService:
    return TomTomService()


@lru_cache(maxsize=1)
def get_tomtom_incidents() -> TomTomIncidentService:
    """TomTom Traffic Incident Details client (separate from the Flow client returned by get_tomtom)."""
    return TomTomIncidentService()


def _incident_graph_loader():
    """(graph, source, is_real_osm) for incident matching. Never starts a slow OSM download."""
    provider = get_provider()
    try:
        provider.require_graph()          # cached real OSM graph only; never the synthetic fallback, never a download
    except NetworkUnavailableError:
        return None, "unavailable", False
    return provider.graph, provider.source, bool(provider.graph is not None and provider.is_real_osm)


@lru_cache(maxsize=1)
def get_incident_sync() -> IncidentSyncService:
    return IncidentSyncService(get_tomtom_incidents(), get_incidents(), _incident_graph_loader)


@lru_cache(maxsize=1)
def get_traffic_provider():
    """Provider selected by TRAFFIC_PROVIDER in .env."""
    return create_provider()


def get_optimizer() -> RouteOptimizer:
    """Optimizer on the ONE shared road graph. Raises NetworkUnavailableError (HTTP 503) instead of using synthetic data."""
    provider = get_provider()
    graph = provider.require_graph()
    return RouteOptimizer(
        graph,
        provider.source,
        get_incidents()
    )


@lru_cache(maxsize=1)
def get_copilot() -> TrafficCopilot:
    return TrafficCopilot(
        traffic_service=get_traffic(),
        forecast_service=get_forecasts(),
        weather_service=get_weather(),
        route_optimizer_factory=get_optimizer,
        incident_manager=get_incidents(),
        tomtom_service=get_tomtom(),
        incident_sync=get_incident_sync(),
    )
