from __future__ import annotations
from typing import Any, Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RouteRequest(BaseModel):
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)
    destination_lat: float | None = Field(default=None, ge=-90, le=90)
    destination_lon: float | None = Field(default=None, ge=-180, le=180)
    origin_name: str | None = None
    destination_name: str | None = None
    origin_node: str | None = None
    destination_node: str | None = None
    algorithm: Literal["dijkstra", "astar"] = "dijkstra"
    alternatives: int = Field(default=3, ge=1, le=5)
    rainfall_mm: float = Field(default=0.0, ge=0, le=100)
    use_simulated_congestion: bool = False
    use_tomtom_flow: bool = Field(default=False, description="Probe TomTom flow along the route corridor and apply measurements matched to directed OSM edges (needs TOMTOM_API_KEY).")
    use_live_traffic: bool = Field(default=False, description="Use the provider selected by TRAFFIC_PROVIDER (HERE by default): fetch measured flow for the route corridor, match it to directed OSM edges and re-route. Also attaches the provider's own ETA (traffic vs free-flow) for comparison. Needs the provider key in backend/.env.")
    departure_time: datetime | None = None

    @model_validator(mode="after")
    def validate_endpoints(self):
        coordinate_mode = all(v is not None for v in [self.origin_lat, self.origin_lon, self.destination_lat, self.destination_lon])
        name_mode = bool(self.origin_name and self.destination_name)
        node_mode = bool(self.origin_node and self.destination_node)
        if sum([coordinate_mode, name_mode, node_mode]) != 1:
            raise ValueError("Provide exactly one endpoint pair: coordinates, names, or graph node IDs")
        return self


class RoutePlanRequest(RouteRequest):
    """Incident-aware route planning (POST /routes/plan). Endpoint rules are inherited from RouteRequest."""
    refresh_incidents: bool = Field(default=True, description="Ask the TomTom incident service for fresh data first (rate-limited server-side; never required).")
    exclude_sources: list[Literal["tomtom", "manual", "simulation"]] = Field(default_factory=list, description="Incident sources to ignore, e.g. ['simulation'] to plan with live data only.")


class IncidentCreate(BaseModel):
    edge_u: str
    edge_v: str
    kind: Literal["accident", "road_closure", "road_work", "obstruction", "hazard", "traffic_jam"] = "accident"
    severity: float = Field(default=0.5, ge=0, le=1)
    # "tomtom" is deliberately not accepted here: provider incidents only come from the TomTom feed.
    source: Literal["manual", "simulation"] = "manual"
    expires_in_seconds: int = Field(default=3600, ge=0, le=604800)
    bidirectional: bool = False
    description: str = ""


class ForecastRequest(BaseModel):
    location: str
    horizon_minutes: Literal[30, 60] = 30


class CopilotRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class PredictionRequest(BaseModel):
    prediction_timestamp: str | None = None
    use_open_meteo_weather: bool = False
    allow_simulated_weather_fallback: bool = False
    vehicle_count: float | None = Field(default=None, ge=0)
    avg_speed: float | None = Field(default=None, ge=0, le=250)
    rainfall: float | None = Field(default=None, ge=0)
    clouds: float | None = Field(default=None, ge=0, le=100)
    temperature: float | None = None


# ---------------------------------------------------------------------------------------------------------------------
# Live incident feed (TomTom Traffic Incident Details) - response models
# ---------------------------------------------------------------------------------------------------------------------
IncidentSource = Literal["manual", "simulation", "tomtom"]
ProviderState = Literal["live", "live_empty", "cached", "disabled_no_key", "disabled", "unavailable", "idle"]


class ProviderError(BaseModel):
    type: str
    message: str
    http_status: int | None = None
    at: str | None = None


class IncidentProviderStatus(BaseModel):
    """State of the TomTom incident service. ``is_live`` is true only for a recent successful fetch."""
    model_config = ConfigDict(extra="allow")
    provider: str = "tomtom"
    feed: str = "traffic_incident_details_v5"
    state: ProviderState
    message: str
    enabled: bool
    configured: bool
    is_live: bool
    using_cached_data: bool
    stale: bool
    incident_count: int
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    data_age_seconds: float | None = None
    last_error: ProviderError | None = None
    consecutive_failures: int = 0
    poll_interval_seconds: float
    min_refresh_seconds: float
    stale_after_seconds: float
    next_allowed_refresh_at: str | None = None
    bbox: list[float]
    bbox_area_km2: float
    counts: dict[str, int] | None = None
    last_sync: dict[str, Any] | None = None
    background_polling: bool = False
    attribution: str


class IncidentCounts(BaseModel):
    manual: int = 0
    simulation: int = 0
    tomtom: int = 0
    tomtom_matched: int = 0
    tomtom_unmatched: int = 0
    tomtom_applied_to_routing: int = 0
    total: int = 0


class FeedEdge(BaseModel):
    u: str
    v: str
    key: str | None = None
    road_name: str | None = None
    length_m: float | None = None


class FeedIncident(BaseModel):
    """One incident as shown to a user. For TomTom this is one PROVIDER incident (matched to 0..n directed edges)."""
    model_config = ConfigDict(extra="allow")
    id: str
    source: IncidentSource
    kind: str
    description: str = ""
    severity: float | None = None
    severity_source: str | None = None
    status: Literal["applied", "matched_not_applied", "unmatched", "stale"]
    matched: bool
    routing_applied: bool
    confirmed: bool = True
    stale: bool = False
    start_time: str | None = None
    end_time: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    last_seen_at: str | None = None
    provider_id: str | None = None
    edges: list[FeedEdge] = []
    bidirectional: bool = False
    geometry: dict[str, Any] | None = None
    latitude: float | None = None
    longitude: float | None = None
    match: dict[str, Any] | None = None
    provider: dict[str, Any] | None = None


class IncidentsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    incidents: list[dict[str, Any]]      # legacy: edge-level records of every source (what routing prices)
    source_types: list[str]
    feed: list[FeedIncident]
    counts: IncidentCounts
    provider: IncidentProviderStatus
    data_notice: str


class IncidentRefreshResponse(BaseModel):
    refreshed: bool = Field(description="True when this call completed a successful TomTom request")
    throttled: bool = Field(description="True when no request was made (min interval, back-off, disabled)")
    sync: dict[str, Any] | None = None
    provider: IncidentProviderStatus
    counts: IncidentCounts
    feed: list[FeedIncident]


class TomTomIncidentsResponse(BaseModel):
    provider: IncidentProviderStatus
    incidents: list[FeedIncident]
    matched: int
    unmatched: int
    note: str


class IncidentImpactResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    rerouted: bool
    reason: str
    without_live_incidents: dict[str, Any]
    with_incidents: dict[str, Any]
    incidents_on_original_route: list[FeedIncident]
    incidents_on_new_route: list[FeedIncident]
    delta: dict[str, float | None]
    provider: IncidentProviderStatus
