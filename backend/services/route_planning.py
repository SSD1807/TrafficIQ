"""Framework-free orchestration for /routes/plan and /locations/search (so it can be tested without FastAPI).

Raises :class:`PlanningError` (status + structured detail) for every expected failure; the API layer only translates it.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import networkx as nx

from routing.geocoding import GeocodeError
from routing.osm_network import haversine_m

LOG = logging.getLogger("trafficiq.planning")


class PlanningError(Exception):
    def __init__(self, status: int, detail: dict[str, Any]) -> None:
        super().__init__(detail.get("message", "planning error"))
        self.status, self.detail = status, detail


def resolve_node(graph, node_key: str):
    for node in graph.nodes:
        if str(node) == str(node_key):
            return node
    raise PlanningError(422, {"code": "unknown_node", "message": f"Road graph node '{node_key}' does not exist in the loaded network."})


def geocode_error(exc: GeocodeError) -> PlanningError:
    return PlanningError({"geocoder_unavailable": 503, "not_found": 404}.get(exc.code, 422), exc.to_public())


def locate(spec: dict[str, Any], graph, optimizer, labeler, *, max_snap_m: float = 1000.0) -> dict[str, Any]:
    """Resolve one endpoint (``node`` id | ``name`` | ``lat``/``lon``) to a node of the loaded graph, with a readable label."""
    geocode = None
    if spec.get("node"):
        node = resolve_node(graph, spec["node"])
        lat, lon, given = float(graph.nodes[node]["y"]), float(graph.nodes[node]["x"]), f"node {spec['node']}"
    elif spec.get("name"):
        try:
            geocode = optimizer.geocode_detail(spec["name"])
        except GeocodeError as exc:
            raise geocode_error(exc) from exc
        except ValueError as exc:            # coordinates validated outside the graph bounds
            raise PlanningError(422, {"code": "outside_network", "message": str(exc)}) from exc
        lat, lon, given = geocode.latitude, geocode.longitude, spec["name"]
        node = optimizer.nearest_node(lat, lon)
    else:
        lat, lon, given = spec.get("lat"), spec.get("lon"), "map coordinates"
        try:
            node = optimizer.nearest_node(lat, lon)
        except ValueError as exc:
            raise PlanningError(422, {"code": "outside_network", "message": str(exc)}) from exc
    if node not in graph:
        raise PlanningError(422, {"code": "node_not_in_graph", "message": f"Matched node {node} is not part of the loaded road graph."})
    snap = haversine_m(lat, lon, float(graph.nodes[node]["y"]), float(graph.nodes[node]["x"]))
    if snap > max_snap_m:
        raise PlanningError(422, {"code": "no_road_nearby", "hint": "Choose a point closer to a road, or a more specific place name.",
                                  "message": f"'{given}' is {round(snap)} m from the nearest drivable road in the loaded network (limit {int(max_snap_m)} m)."})
    return {"input": given, "node": node, "node_id": str(node), "snap_distance_m": round(snap, 1), "latitude": lat, "longitude": lon,
            "road_label": labeler.label_node(node), "geocode": geocode.to_public() if geocode else None}


def public_place(place: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in place.items() if k != "node"}


def plan_route(*, engine, optimizer, sync, provider, incidents, origin: dict, destination: dict, algorithm: str, alternatives: int,
               rainfall_mm: float, congestion: dict | None, exclude_sources: list[str], refresh_incidents: bool,
               departure: datetime, max_snap_m: float = 1000.0) -> dict[str, Any]:
    graph = engine.graph
    start = locate(origin, graph, optimizer, engine.labeler, max_snap_m=max_snap_m)
    end = locate(destination, graph, optimizer, engine.labeler, max_snap_m=max_snap_m)
    if start["node_id"] == end["node_id"]:
        raise PlanningError(422, {"code": "same_node", "message": "Origin and destination resolve to the same road node."})
    refresh = None
    if refresh_incidents:
        try:
            refresh = sync.refresh()
        except Exception as exc:                      # incident problems must never block routing
            LOG.exception("Incident refresh failed during route planning")
            refresh = {"refreshed": False, "error": type(exc).__name__}
    try:
        result = engine.plan(start["node"], end["node"], algorithm=algorithm, alternatives=alternatives, congestion_by_edge=congestion,
                             rainfall_mm=rainfall_mm, exclude_sources=exclude_sources)
    except nx.NetworkXNoPath as exc:
        raise PlanningError(404, {"code": "no_route", "message": "No drivable route connects the two locations in the loaded road network."}) from exc
    except ValueError as exc:
        raise PlanningError(400, {"code": "bad_request", "message": str(exc)}) from exc
    provider_status = sync.provider.status()
    state = provider_status.get("state")
    supported = state in ("live", "live_empty", "cached")
    on_route = any(r.get("affects_original_route") for r in result["incident_analysis"])
    net = provider.status()
    result.update(
        origin=public_place(start), destination=public_place(end), network=net,
        incident_data={"provider": provider_status, "refresh": refresh, "counts": incidents.counts(), "live_data_supported": supported,
                       "incident_free_claim_supported": bool(supported and not on_route),
                       "note": ("Live TomTom incident data was used." if supported else
                                f"Live TomTom incident data is NOT available (state: {state}); only manual/simulated incidents, if any, were "
                                "considered. The route must not be read as incident-free.")},
        departure_time=departure.isoformat(),
        data_sources={"road_network": "OpenStreetMap (cached)" if provider.is_real_osm else provider.source,
                      "incidents": "TomTom live" if supported else f"TomTom {state}",
                      "traffic_costs": "simulated" if congestion else "free-flow OSM road attributes",
                      "weather": "request rainfall_mm" if rainfall_mm else "none applied"},
        notice=("Travel times are model estimates from OSM road attributes plus the incident delays reported by the provider (or the "
                "documented rule defaults, labelled per incident). They are not live per-road speed predictions."))
    return result
