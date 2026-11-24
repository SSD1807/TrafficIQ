"""Incident-aware rerouting analysis shared by the API (/routes/incident-impact) and the AI Copilot.

Runs the EXISTING Dijkstra / A* optimizer twice on the same graph and costs:
  * without live TomTom incidents (manual + simulated incidents still apply), and
  * with every active incident,
then reports whether the best route changed and which incidents lie on each route.
"""
from __future__ import annotations

from typing import Any

import networkx as nx

from routing.route_optimizer import RouteOptimizer


def incidents_on_route(route: dict | None, manager) -> list[dict[str, Any]]:
    """Feed entries (any source) whose directed edges the given route actually traverses."""
    if not route or manager is None or not hasattr(manager, "feed"):
        return []
    seq = [str(n) for n in route.get("node_sequence", [])]
    traversed = set(zip(seq[:-1], seq[1:]))
    effects = manager.edge_effects()
    hit_ids = {(f"tomtom:{i['provider_id']}" if i.get("provider_id") else i["id"])
               for edge in traversed for i in effects.get(edge, [])}
    return [e for e in manager.feed() if e["id"] in hit_ids]


def compute_incident_impact(optimizer: RouteOptimizer, start, end, *, algorithm: str = "dijkstra", alternatives: int = 3,
                            congestion_by_edge: dict | None = None, rainfall_mm: float = 0.0) -> dict[str, Any]:
    manager = optimizer.incident_manager
    kw = dict(algorithm=algorithm, alternatives=alternatives, congestion_by_edge=congestion_by_edge, rainfall_mm=rainfall_mm)
    hide_live = manager.view({"tomtom"}) if manager is not None and hasattr(manager, "view") else manager
    without = RouteOptimizer(optimizer.graph, optimizer.graph_source, hide_live).route_nodes(start, end, **kw)
    try:
        with_inc = optimizer.route_nodes(start, end, **kw)
    except nx.NetworkXNoPath:
        with_inc = {"routes": [], "error": "No drivable route remains when the active incidents (e.g. closures) are applied."}
    base_best = without["routes"][0]
    new_best = with_inc["routes"][0] if with_inc.get("routes") else None
    on_original = incidents_on_route(base_best, manager)
    on_new = incidents_on_route(new_best, manager) if new_best else []
    rerouted = bool(new_best and new_best["node_sequence"] != base_best["node_sequence"])
    if new_best is None:
        reason = "All routes are blocked by active incidents."
    elif rerouted:
        reason = f"The fastest route without live incidents crosses {len(on_original)} active incident(s); a different route is now fastest."
    elif on_original:
        reason = "The route crosses active incident(s) but remains the fastest option after their delay is priced in."
    else:
        reason = "No active incident affects the fastest route."
    delta: dict[str, float | None] = {"travel_time_min": None, "distance_km": None}
    if new_best:
        delta = {"travel_time_min": round(new_best["estimated_travel_time_min"] - base_best["estimated_travel_time_min"], 2),
                 "distance_km": round(new_best["distance_km"] - base_best["distance_km"], 3)}
    return {"rerouted": rerouted, "reason": reason, "without_live_incidents": without, "with_incidents": with_inc,
            "incidents_on_original_route": on_original, "incidents_on_new_route": on_new, "delta": delta}
