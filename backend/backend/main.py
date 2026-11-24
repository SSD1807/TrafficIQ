"""FastAPI backend for the Traffic Bottleneck Prediction & Smart Routing System."""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import networkx as nx
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.dependencies import (
    ROOT, get_copilot, get_forecasts, get_incident_sync, get_incidents, get_optimizer,
    get_provider, get_traffic, get_tomtom, get_tomtom_incidents, get_traffic_provider, get_weather,
)
from backend.schemas.api import (
    RouteRequest, RoutePlanRequest, IncidentCreate, ForecastRequest, CopilotRequest, PredictionRequest,
    IncidentsResponse, IncidentRefreshResponse, IncidentImpactResponse, IncidentProviderStatus, TomTomIncidentsResponse,
)
from routing.incident_impact import compute_incident_impact, incidents_on_route
from routing.engine import IncidentRoutingEngine
from routing.geocoding import GeocodeError
from routing.labels import LocationLabeler
from routing.impact_rules import get_rules
from routing.osm_network import NetworkUnavailableError
from services.route_planning import PlanningError, locate, plan_route, public_place
from routing.route_optimizer import RouteOptimizer
from simulation.traffic_simulator import TrafficSimulator
from services.osrm_service import OSRMService

log = logging.getLogger("trafficiq.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Optionally poll TomTom incidents in the background (needs TOMTOM_API_KEY; never started without it)."""
    sync = None
    if os.getenv("TOMTOM_INCIDENTS_BACKGROUND_POLL", "true").strip().lower() not in {"0", "false", "no"}:
        try:
            sync = get_incident_sync()
            if sync.start_background():
                log.info("TomTom incident background polling started (every %ss).", sync.provider.config.poll_interval_seconds)
        except Exception:  # the API must still start if the incident service cannot
            log.exception("Could not start TomTom incident polling")
            sync = None
    yield
    if sync is not None:
        sync.stop_background()


app = FastAPI(
    lifespan=lifespan,
    title="Traffic Bottleneck Prediction & Smart Routing API",
    description=("API for next-30-minute synthetic-data congestion classification, "
                 "30/60-minute numeric forecasts, OSM-based routing, incident simulation and weather."),
    version="2.0.0",
)
# Streamlit (8501) origins are retained; Vite/React dev + preview origins were added for the merged React frontend.
# Extra origins can be supplied (comma-separated) via CORS_ALLOW_ORIGINS in backend/.env.
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:8501", "http://127.0.0.1:8501",
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]
_EXTRA_CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_DEFAULT_CORS_ORIGINS + _EXTRA_CORS_ORIGINS,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


@app.get("/health")
def health():
    import importlib.util
    import os

    traffic = get_traffic()
    provider = get_provider()
    openai_key = bool(os.getenv("OPENAI_API_KEY"))
    tomtom_key = bool(os.getenv("TOMTOM_API_KEY"))
    return {
        "status": "ok" if traffic.model is not None else "degraded",
        "service": "traffic-bottleneck-api", "version": "2.0.0",
        "classifier_available": traffic.model is not None,
        "forecast_artifacts_available": (ROOT / "models" / "forecasting").exists(),
        "road_network_loaded": provider.graph is not None,
        "openai_enabled": openai_key,
        "tomtom_enabled": tomtom_key,
        "data_sources": {
            "traffic": {"status": "historical_synthetic" if not traffic.data.empty else "unavailable",
                        "source": "synthetic_bhubaneswar_dataset" if not traffic.data.empty else "none", "is_live": False},
            "road_network": {"status": provider.source, "source": provider.source,
                             "is_live": False, "is_real_road_geometry": provider.source.startswith("openstreetmap"),
                             "osmnx_installed": importlib.util.find_spec("osmnx") is not None},
            "weather": {"status": "not_requested", "source": "open_meteo_public_api", "is_live": False},
            "tomtom": {"status": "configured_not_tested" if tomtom_key else "unavailable_no_api_key",
                       "source": "tomtom_traffic_api", "is_live": False},
            "tomtom_incidents": {"status": get_tomtom_incidents().status()["state"],
                                 "source": "tomtom_traffic_incident_details_v5",
                                 "is_live": get_tomtom_incidents().status()["is_live"],
                                 "note": "Reflects the last background/UI refresh; /health itself makes no external request."},
            "traffic_provider": {**get_traffic_provider().status(), "status": get_traffic_provider().status()["state"],
                                 "source": f"{get_traffic_provider().name}_traffic_api", "is_live": False,
                                 "note": "Configuration state only; no external request is made by /health."},
            "copilot": {"status": "configured_not_tested" if openai_key and importlib.util.find_spec("openai") else
                                   ("configured_sdk_missing" if openai_key else "rule_based_fallback"),
                        "source": "openai_responses_api" if openai_key else "local_rule_based", "is_live": False},
        },
        "notice": "Health reports configuration and loaded state. It does not perform external provider requests."
    }


@app.get("/locations")
def locations():
    return {"locations": get_traffic().list_locations(), "source": "synthetic_bhubaneswar_dataset", "is_live": False}


@app.get("/traffic/current")
def current_traffic():
    return get_traffic().get_current_state()


@app.post("/traffic/predict/{location}")
def predict_traffic(location: str, request: PredictionRequest | None = None):
    request = request or PredictionRequest()
    overrides = {k: v for k, v in {
        "vehicle_count": request.vehicle_count, "avg_speed": request.avg_speed,
        "rainfall": request.rainfall, "clouds": request.clouds, "temperature": request.temperature,
    }.items() if v is not None}
    weather_source = None
    if request.use_open_meteo_weather:
        weather = get_weather().get_current(allow_simulated_fallback=request.allow_simulated_weather_fallback)
        if weather.get("status") in {"ok", "stale_cache", "simulated_fallback"}:
            if weather.get("rain_mm") is not None: overrides["rainfall"] = weather.get("rain_mm")
            if weather.get("cloud_cover_pct") is not None: overrides["clouds"] = weather.get("cloud_cover_pct")
            if weather.get("temperature_c") is not None: overrides["temperature"] = weather.get("temperature_c")
            weather_source = weather.get("source")
        else:
            weather_source = "unavailable; historical synthetic weather retained"
    try:
        return get_traffic().predict_next(location, request.prediction_timestamp, overrides, weather_source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/traffic/predictions")
def all_predictions():
    return {"predictions": get_traffic().predict_all(), "source": "synthetic_bhubaneswar_dataset", "is_live": False}


@app.get("/forecasts/{location}")
def forecast(location: str, horizon_minutes: int = Query(30, ge=30, le=60),
             model: str = Query("random_forest", description="Regressor family: random_forest or xgboost")):
    if horizon_minutes not in (30, 60):
        raise HTTPException(400, "horizon_minutes must be 30 or 60")
    try:
        return get_forecasts().forecast(location, horizon_minutes, model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


_LABELERS: dict[int, LocationLabeler] = {}


def get_engine() -> IncidentRoutingEngine:
    """Shared incident-aware engine over the ONE loaded graph, incident registry and impact rules (same for every endpoint)."""
    optimizer = get_optimizer()
    key = id(optimizer.graph)
    labeler = _LABELERS.get(key)
    if labeler is None:
        _LABELERS.clear()
        labeler = _LABELERS[key] = LocationLabeler(optimizer.graph, optimizer.geocoder())
    return IncidentRoutingEngine(optimizer.graph, optimizer.graph_source, get_incidents(), labeler=labeler)


def _geocode_http(exc: GeocodeError) -> HTTPException:
    status = {"geocoder_unavailable": 503, "not_found": 404}.get(exc.code, 422)
    return HTTPException(status, detail=exc.to_public())


@app.exception_handler(NetworkUnavailableError)
async def _network_unavailable(_request, exc: NetworkUnavailableError):
    """The real OSM network is missing: say so (503) - never answer from a synthetic graph."""
    log.warning("Road network unavailable: %s", exc.reason)
    return JSONResponse(status_code=503, content={"detail": exc.to_public()})


def _resolve_node(graph, node_key: str):
    for node in graph.nodes:
        if str(node) == str(node_key):
            return node
    raise ValueError(f"Road graph node '{node_key}' does not exist")


def _provider_eta(graph, start, end) -> dict[str, Any]:
    """Provider-side ETA (traffic vs free-flow) for the same endpoints; reference only, never replaces the OSM route time."""
    provider = get_traffic_provider()
    if not getattr(provider, "supports_route_eta", False):
        return {"provider": provider.name, "status": "unsupported", "data_state": "unavailable", "reference_only": True,
                "note": f"{provider.name} does not provide route ETAs in this app."}
    a, b = graph.nodes[start], graph.nodes[end]
    return provider.route_eta((float(a["y"]), float(a["x"])), (float(b["y"]), float(b["x"]))).to_public()


@app.get("/traffic/provider/status")
def traffic_provider_status():
    """Selected provider + configuration/runtime state. Makes no external request."""
    return get_traffic_provider().status()


@app.get("/traffic/flow")
def traffic_flow(min_lon: float = 85.74, min_lat: float = 20.22, max_lon: float = 85.92, max_lat: float = 20.37,
                 include_segments: bool = True):
    """Colour-coded flow segments for the map. Always labelled with data_state: live | cached | stale_cache | unavailable | disabled."""
    try:
        return get_traffic_provider().fetch_flow(min_lon, min_lat, max_lon, max_lat).to_public(include_segments)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/traffic/tiles/{z}/{x}/{y}.png")
def traffic_tile(z: int, x: int, y: int):
    """Server-side proxy for the provider's traffic tile layer so the API key never reaches the browser."""
    tile = get_traffic_provider().tile(z, x, y)
    headers = {"X-Traffic-Data-State": tile.data_state, "X-Traffic-Status": tile.status, "Cache-Control": "public, max-age=60",
               "Access-Control-Expose-Headers": "X-Traffic-Data-State, X-Traffic-Status"}
    if tile.content:
        return Response(content=tile.content, media_type=tile.content_type, headers=headers)
    return Response(status_code=204, headers=headers)  # empty tile: nothing is drawn, never a fake overlay


@app.get("/routing/nodes")
def routing_nodes(limit: int = Query(250, ge=1, le=2000), q: str | None = Query(None, description="Filter by road/junction label"),
                  named_only: bool = Query(True, description="Only nodes whose label comes from OSM road names")):
    """Road-graph nodes with human-readable labels. ``id`` is the OSM node id (used internally by routing); ``label`` is for people."""
    engine = get_engine(); provider = get_provider()
    labeler, needle = engine.labeler, (q or "").strip().lower()
    nodes, unnamed = [], []
    for node, attrs in engine.graph.nodes(data=True):
        info = labeler.label_node(node)
        if needle and needle not in info["label"].lower():
            continue
        row = {"id": str(node), "name": info["label"], "label": info["label"], "road_names": info["road_names"],
               "label_source": info["label_source"], "latitude": attrs.get("y"), "longitude": attrs.get("x")}
        if info["label_source"] == "coordinates":
            if len(unnamed) < limit:
                unnamed.append(row)
            continue
        nodes.append(row)
        if len(nodes) >= limit:
            break
    if not named_only:
        nodes = (nodes + unnamed)[:limit]
    elif not nodes:
        nodes = unnamed          # a graph with no named roads at all is still listed (coordinate labels) rather than returning nothing
    return {"graph_source": provider.source, "is_real_road_geometry": provider.is_real_osm, "nodes": nodes,
            "note": "Labels come from OSM road names near each node; nodes without one are hidden unless named_only=false."}


@app.get("/network/status")
def network_status():
    """Road-network loading status. Reads the cache lazily; never downloads, never substitutes synthetic data."""
    return get_provider().status()


@app.get("/network/roads")
def network_roads(level: str = Query("major", pattern="^(major|all)$"), limit: int = Query(6000, ge=1, le=30000),
                  min_lat: float | None = None, min_lon: float | None = None, max_lat: float | None = None, max_lon: float | None = None):
    """Actual OSM road geometry of the loaded network for the map (de-duplicated; ``truncated`` tells if ``limit`` cut it)."""
    from routing.network_geometry import road_segments
    graph = get_provider().require_graph()
    bbox = (min_lat, min_lon, max_lat, max_lon) if None not in (min_lat, min_lon, max_lat, max_lon) else None
    return {"graph_source": get_provider().source, "is_real_road_geometry": get_provider().is_real_osm, **road_segments(graph, bbox=bbox, level=level, limit=limit)}


@app.post("/network/download")
def network_download():
    """Download the drivable OSM network (needs internet + osmnx) and cache it. Fails with a readable reason, never with fake data."""
    provider = get_provider()
    try:
        provider.download()
    except Exception as exc:
        raise HTTPException(503, detail={"code": "osm_download_failed", "message": "The OpenStreetMap network could not be downloaded.",
                                         "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
                                         "hint": "Check the internet connection / Overpass availability and pip install osmnx, then retry."}) from exc
    return provider.status()


MAX_SNAP_M = float(os.getenv("MAX_NODE_SNAP_DISTANCE_M", "1000"))


@app.exception_handler(PlanningError)
async def _planning_error(_request, exc: PlanningError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


def _spec(request: RouteRequest, which: str) -> dict[str, Any]:
    return {"node": getattr(request, f"{which}_node"), "name": getattr(request, f"{which}_name"),
            "lat": getattr(request, f"{which}_lat"), "lon": getattr(request, f"{which}_lon")}


@app.get("/locations/search")
def locations_search(q: str = Query(..., min_length=1, max_length=200)):
    """Geocode a place/road/landmark/address in Bhubaneswar and snap it to the nearest node of the loaded road network."""
    engine = get_engine()
    return public_place(locate({"name": q}, engine.graph, get_optimizer(), engine.labeler, max_snap_m=MAX_SNAP_M))


@app.post("/routes/plan")
def plan_route_endpoint(request: RoutePlanRequest):
    """Incident-aware route planning on the shared engine: normal route -> live incidents -> impact -> alternatives -> decision."""
    engine, optimizer = get_engine(), get_optimizer()
    departure = request.departure_time or datetime.now(ZoneInfo("Asia/Kolkata"))
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    congestion = TrafficSimulator().edge_conditions(engine.graph, departure, rainfall_mm=request.rainfall_mm) if request.use_simulated_congestion else None
    return plan_route(engine=engine, optimizer=optimizer, sync=get_incident_sync(), provider=get_provider(), incidents=get_incidents(),
                      origin=_spec(request, "origin"), destination=_spec(request, "destination"), algorithm=request.algorithm,
                      alternatives=request.alternatives, rainfall_mm=request.rainfall_mm, congestion=congestion,
                      exclude_sources=list(request.exclude_sources), refresh_incidents=request.refresh_incidents, departure=departure,
                      max_snap_m=MAX_SNAP_M)


@app.get("/incidents/analysis")
def incidents_analysis(refresh: bool = Query(False, description="Fetch TomTom first (rate-limited server-side)")):
    """Every active incident with matching status, impact class, routing effect and readable road names (shared engine)."""
    sync = get_incident_sync()
    if refresh:
        sync.refresh()
    engine = get_engine()
    return {"incidents": engine.analyse_feed(), "counts": get_incidents().counts(), "provider": sync.provider.status(),
            "network": {k: v for k, v in get_provider().status().items() if k in ("state", "source", "is_real_osm", "nodes", "edges")},
            "rules": get_rules().to_public(), "data_notice": _DATA_NOTICE}


@app.get("/incidents/impact-rules")
def incident_impact_rules():
    """The configurable impact rules currently in force (documented in routing/impact_rules.py)."""
    return get_rules().to_public()
def _resolve_endpoints(request: RouteRequest, graph, optimizer):
    if request.origin_node and request.destination_node:
        start = _resolve_node(graph, request.origin_node)
        end = _resolve_node(graph, request.destination_node)
        return start, end, request.origin_node, request.destination_node
    if request.origin_name and request.destination_name:
        try:
            origin = optimizer.geocode_place(request.origin_name)
            destination = optimizer.geocode_place(request.destination_name)
        except GeocodeError as exc:
            raise _geocode_http(exc) from exc
        return (optimizer.nearest_node(*origin), optimizer.nearest_node(*destination),
                request.origin_name, request.destination_name)
    return (optimizer.nearest_node(request.origin_lat, request.origin_lon),
            optimizer.nearest_node(request.destination_lat, request.destination_lon),
            "coordinate origin", "coordinate destination")


@app.post("/routes/calculate")
def calculate_route(request: RouteRequest):
    try:
        provider = get_provider(); graph = provider.require_graph()
        optimizer = get_optimizer()
        start, end, origin_name, destination_name = _resolve_endpoints(request, graph, optimizer)
        congestion = None
        departure = request.departure_time or datetime.now(ZoneInfo("Asia/Kolkata"))
        if departure.tzinfo is None:
            departure = departure.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        if request.use_simulated_congestion:
            congestion = TrafficSimulator().edge_conditions(graph, departure, rainfall_mm=request.rainfall_mm)
        route_kwargs = dict(algorithm=request.algorithm, alternatives=request.alternatives,
                            congestion_by_edge=congestion, rainfall_mm=request.rainfall_mm)
        if request.use_live_traffic:
            from routing.traffic_routing import route_with_provider_flow
            data = route_with_provider_flow(optimizer, get_traffic_provider(), start, end, **route_kwargs)
            data["provider_eta"] = _provider_eta(graph, start, end)
        elif request.use_tomtom_flow:
            from routing.tomtom_routing import route_with_tomtom_flow
            data = route_with_tomtom_flow(optimizer, get_tomtom(), start, end, **route_kwargs)
        else:
            data = optimizer.route_nodes(start, end, **route_kwargs)
        if "traffic_flow" not in data:
            selected_provider = (
                "tomtom" if request.use_tomtom_flow
                else get_traffic_provider().name
            )
            data["traffic_flow"] = {
                "provider": selected_provider,
                "source": f"{selected_provider}_traffic_api",
                "status": "disabled",
                "is_live": False,
                "edges_matched": 0,
                "measurements_matched": 0,
                "coverage_best_route": 0.0,
                "note": "No traffic measurements were applied; route uses modelled or simulated costs."
            }
        manager = getattr(optimizer, "incident_manager", None)
        if manager is not None and hasattr(manager, "counts") and data.get("routes"):
            data["incident_summary"] = {
                "applied": {k: v for k, v in manager.counts().items() if k in ("manual", "simulation", "tomtom_applied_to_routing")},
                "on_best_route": incidents_on_route(data["routes"][0], manager),
            }
        data["origin_input"] = origin_name; data["destination_input"] = destination_name
        data["departure_time"] = departure.isoformat()
        data["departure_time_source"] = "request" if request.departure_time else "Asia/Kolkata server-local default"
        data["estimated_arrival_time"] = (departure + timedelta(minutes=data["routes"][0]["estimated_travel_time_min"])).isoformat() if data.get("routes") else None
        data["traffic_cost_mode"] = "simulated_departure_time_traffic" if request.use_simulated_congestion else "free-flow-road-attributes-plus-incidents"
        flow = data.get("traffic_flow") or {}
        if flow.get("status") == "applied":
            data["traffic_cost_mode"] = (f"{flow.get('provider', 'tomtom')}_measured_on_matched_edges_plus_model"
                                         if request.use_live_traffic else "tomtom_measured_on_matched_edges_plus_model")
        data["notice"] = ("OpenStreetMap provides road geometry/attributes, not live traffic speeds. "
                          "Congestion costs are simulated unless provider (HERE/TomTom) measurements were retrieved AND matched to road edges "
                          "(see traffic_flow.status == 'applied' and routes[].measured_distance_share).")
        return data
    except nx.NetworkXNoPath as exc:
        raise HTTPException(404, "No drivable route exists between the selected points") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


_DATA_NOTICE = ("TomTom incidents are live provider reports (when the status is live/cached); manual and simulated "
                "incidents are user-created test data. Road matching uses OpenStreetMap geometry.")


_ENRICH_KEYS = ("road_name", "road_name_source", "location_label", "impact_class", "routing_effect", "impact_score", "explanation",
                "unmatched_reason", "matched_length_m", "provider_length_m", "provider_delay_s", "blocked_lanes", "category", "magnitude_label")


def _enriched_feed() -> list[dict]:
    """Unified feed + the shared engine's analysis (readable location, impact class, routing effect). Falls back to the plain feed
    when the road network is unavailable (the incidents are then shown unmatched, with the network reason)."""
    feed = get_incidents().feed()
    try:
        rows = {r["id"]: r for r in get_engine().analyse_feed()}
    except NetworkUnavailableError:
        return feed
    except Exception:  # analysis is an enrichment: it must never take the incident feed down
        log.exception("Incident analysis failed; returning the plain feed")
        return feed
    return [{**e, **{k: rows[e["id"]][k] for k in _ENRICH_KEYS if k in rows[e["id"]]}} if e["id"] in rows else e for e in feed]


def _incident_payload() -> dict:
    manager = get_incidents()
    return {"feed": _enriched_feed(), "counts": manager.counts(), "provider": get_incident_sync().provider.status()}


@app.get("/incidents", response_model=IncidentsResponse)
def incidents():
    """Active incident feed: manual, simulation and TomTom. ``incidents`` keeps the legacy edge-level records."""
    return {"incidents": get_incidents().list_active(), "source_types": ["manual", "simulation", "tomtom"],
            "data_notice": _DATA_NOTICE, **_incident_payload()}


@app.post("/incidents/refresh", response_model=IncidentRefreshResponse)
def refresh_incidents():
    """Fetch TomTom incidents now (rate-limited server-side), match them to the road graph, reconcile the registry.

    Never raises for provider problems: the response says whether a request was made and what the provider state is.
    Previously fetched incidents are kept if the request fails.
    """
    result = get_incident_sync().refresh()
    payload = _incident_payload()
    return {"refreshed": result["refreshed"], "throttled": result["throttled"], "sync": result["sync"], **payload}


@app.get("/traffic/tomtom/incidents/status", response_model=IncidentProviderStatus)
def tomtom_incidents_status():
    """TomTom incident provider state. Makes no external request."""
    return get_incident_sync().status()


@app.get("/traffic/tomtom/incidents", response_model=TomTomIncidentsResponse)
def tomtom_incidents(refresh: bool = Query(False, description="Fetch from TomTom first (subject to the server-side rate limit)")):
    """Current TomTom incidents (with their road-edge match result). Only returns what TomTom reported; never fabricated."""
    sync = get_incident_sync()
    if refresh or sync.provider.status()["state"] == "idle":
        sync.refresh()
    entries = [e for e in get_incidents().feed() if e["source"] == "tomtom"]
    matched = sum(e["matched"] for e in entries)
    return {"provider": sync.provider.status(), "incidents": entries, "matched": matched, "unmatched": len(entries) - matched,
            "note": "Provider incidents are shown even when they cannot be matched to a road edge; unmatched ones never affect routing."}


@app.post("/incidents", status_code=201)
def create_incident(request: IncidentCreate):
    graph = get_provider().require_graph()
    try:
        u, v = _resolve_node(graph, request.edge_u), _resolve_node(graph, request.edge_v)
        if not graph.has_edge(u, v):
            raise ValueError("The requested directed road segment is not present in the active graph")
        return get_incidents().add(
            u, v, kind=request.kind, severity=request.severity, source=request.source,
            expires_in_seconds=request.expires_in_seconds, bidirectional=request.bidirectional,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/incidents/{incident_id}")
def delete_incident(incident_id: str):
    existing = get_incidents().get(incident_id)
    if existing and existing["source"] == "tomtom":
        raise HTTPException(409, "TomTom incidents are managed by the provider feed and cannot be deleted manually; "
                                 "they disappear when TomTom stops reporting them.")
    removed = get_incidents().remove(incident_id)
    if not removed: raise HTTPException(404, "Incident not found or already expired")
    return {"removed": True, "incident_id": incident_id}


@app.delete("/incidents")
def clear_incidents(source: str | None = None):
    """Clear manual and simulated incidents. Without ``source`` both are cleared; provider incidents are never cleared here."""
    if source is not None and source not in ("manual", "simulation"):
        raise HTTPException(400, "source must be 'manual' or 'simulation'; TomTom incidents are managed by the provider feed")
    manager = get_incidents()
    sources = [source] if source else ["manual", "simulation"]
    return {"removed_count": sum(manager.clear(src) for src in sources)}


@app.post("/routes/incident-impact", response_model=IncidentImpactResponse)
def route_incident_impact(request: RouteRequest):
    """Compare the route WITHOUT live TomTom incidents against the route WITH all active incidents (Dijkstra or A*)."""
    try:
        provider = get_provider(); graph = provider.require_graph()
        optimizer = get_optimizer()
        start, end, _o, _d = _resolve_endpoints(request, graph, optimizer)
        congestion = None
        departure = request.departure_time or datetime.now(ZoneInfo("Asia/Kolkata"))
        if departure.tzinfo is None:
            departure = departure.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        if request.use_simulated_congestion:
            congestion = TrafficSimulator().edge_conditions(graph, departure, rainfall_mm=request.rainfall_mm)
        result = compute_incident_impact(optimizer, start, end, algorithm=request.algorithm, alternatives=request.alternatives,
                                         congestion_by_edge=congestion, rainfall_mm=request.rainfall_mm)
        plan = None
        try:        # the SAME engine as /routes/plan: decision, original/recommended routes, per-incident analysis
            plan = get_engine().plan(start, end, algorithm=request.algorithm, alternatives=request.alternatives,
                                     congestion_by_edge=congestion, rainfall_mm=request.rainfall_mm)
        except nx.NetworkXNoPath:
            plan = {"decision": {"action": "no_route", "reason": "No drivable route remains after applying confirmed closures."}}
        except Exception:
            log.exception("Shared engine plan failed inside /routes/incident-impact")
        return {**result, "plan": plan, "provider": get_incident_sync().provider.status()}
    except nx.NetworkXNoPath as exc:
        raise HTTPException(404, "No drivable route exists between the selected points") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/weather/current")
def current_weather(allow_simulated_fallback: bool = False, force_refresh: bool = False):
    return get_weather().get_current(allow_simulated_fallback=allow_simulated_fallback, force_refresh=force_refresh)


@app.get("/traffic/tomtom/flow")
def tomtom_flow(latitude: float = 20.2961, longitude: float = 85.8245):
    return get_tomtom().flow_at(latitude, longitude)


@app.get("/routes/osrm")
def osrm_route(origin_lat: float, origin_lon: float, destination_lat: float, destination_lon: float):
    """Optional external route comparison; OSRM duration is not a live-traffic ETA."""
    try:
        result = OSRMService().route((origin_lat, origin_lon), (destination_lat, destination_lon))
        result["notice"] = "OSRM route/time is a road-network estimate from the routing service, not a live traffic-speed estimate."
        return result
    except Exception as exc:
        raise HTTPException(502, f"OSRM route request failed: {type(exc).__name__}: {exc}") from exc


@app.post("/copilot/chat")
def copilot_chat(request: CopilotRequest):
    return get_copilot().chat(request.message)


def _read_json_artifact(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


@app.get("/models/evaluation")
def model_evaluation():
    """Read-only view of the SAVED training/evaluation artifacts (added for the React Model Evaluation page).

    Nothing is recomputed and no model is loaded or retrained: the response is the content of
    outputs/phase1_metrics.json, outputs/forecasting/forecast_metrics.json and models/model_metadata.json.
    """
    phase1 = _read_json_artifact(ROOT / "outputs" / "phase1_metrics.json")
    forecast = _read_json_artifact(ROOT / "outputs" / "forecasting" / "forecast_metrics.json")
    metadata = _read_json_artifact(ROOT / "models" / "model_metadata.json")
    if phase1 is None and forecast is None and metadata is None:
        raise HTTPException(503, "No saved evaluation artifacts found; run phase2_train.py and forecasting.forecast_pipeline")
    return {
        "source": "saved_training_artifacts", "is_live": False,
        "dataset_domain": "synthetic Bhubaneswar traffic; not real-world observed measurements",
        "classifier_metadata": metadata,
        "classification": phase1,
        "forecasting": forecast,
        "not_available": ["feature_importance", "training_history", "r2", "lstm"],
        "notice": ("Metrics were computed on a chronological holdout of the bundled SYNTHETIC dataset. "
                   "They do not describe accuracy on real road traffic."),
    }


@app.get("/")
def root():
    return {"message": "Traffic Bottleneck Prediction & Smart Routing API", "docs": "/docs", "health": "/health"}
