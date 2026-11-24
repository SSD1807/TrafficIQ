"""Provider-neutral measured-traffic routing (HERE, TomTom, ...).

Same pipeline as routing/tomtom_routing.py: baseline route -> provider flow for the route corridor -> match to DIRECTED OSM
edges -> re-run Dijkstra/A* with measured costs. Dijkstra/A* and RouteOptimizer are untouched.

Honesty rules (every result carries ``traffic_flow``):
* ``status == "applied"`` only when measurements were matched to at least one directed OSM edge.
* ``traffic_data_state`` is live / cached / stale_cache; only ``live`` and ``cached`` provider data is called live-derived,
  and stale cache is applied but flagged ``is_live: false``.
* Without usable provider data the routes are the unchanged modelled/simulated result and the status says why.
"""
from __future__ import annotations

from typing import Any

from routing.flow_matching import build_measured_edge_table
from routing.tomtom_routing import edge_index_for
from services.traffic_providers.base import STALE, TrafficProvider


def corridor_bbox(routes: list[dict], margin_deg: float = 0.01) -> tuple[float, float, float, float] | None:
    lats: list[float] = []; lons: list[float] = []
    for r in routes:
        for la, lo in r.get("polyline_latlon") or []:
            lats.append(la); lons.append(lo)
    if not lats:
        return None
    return (min(lons) - margin_deg, min(lats) - margin_deg, max(lons) + margin_deg, max(lats) + margin_deg)


def route_with_provider_flow(optimizer, provider: TrafficProvider, origin_node, destination_node, *, algorithm: str = "dijkstra",
                             alternatives: int = 3, congestion_by_edge: dict | None = None, rainfall_mm: float = 0.0,
                             min_confidence: float = 0.5) -> dict[str, Any]:
    kw = dict(algorithm=algorithm, alternatives=alternatives, congestion_by_edge=congestion_by_edge, rainfall_mm=rainfall_mm)
    baseline = optimizer.route_nodes(origin_node, destination_node, **kw)
    info: dict[str, Any] = {"provider": provider.name, "source": f"{provider.name}_traffic", "status": "not_applied", "is_live": False,
                            "traffic_data_state": "unavailable", "edges_matched": 0, "measurements_matched": 0,
                            "coverage_best_route": 0.0, "segments_received": 0, "measurements_received": 0, "age_seconds": None}

    def done(result: dict, **upd) -> dict:
        info.update(upd); result["traffic_flow"] = info; return result

    if not provider.configured:
        return done(baseline, status="disabled", traffic_data_state="disabled", note=f"{provider.name} is not configured (missing key in backend/.env); routes use modelled/simulated costs only.")
    bbox = corridor_bbox(baseline["routes"])
    if bbox is None:
        return done(baseline, status="unavailable", note="Baseline route has no geometry to request traffic for.")
    try:
        flow = provider.corridor_flow(baseline["routes"], bbox)
    except ValueError as exc:  # e.g. corridor larger than the provider's maximum area
        return done(baseline, status="unavailable", note=f"Traffic request not possible for this corridor: {exc}. Routes use modelled costs only.")
    info.update(traffic_data_state=flow.data_state, age_seconds=flow.age_seconds, segments_received=len(flow.segments),
                measurements_received=len(flow.measurements), provider_status=flow.status, source_updated=flow.source_updated)
    if not flow.usable:
        return done(baseline, status="unavailable" if flow.status != "disabled" else "disabled",
                    note=(flow.message or f"{provider.name} returned no usable flow data") + "; routes use modelled/simulated costs only.")
    match_stats: dict[str, int] = {}
    table = build_measured_edge_table(edge_index_for(optimizer.graph), flow.measurements, min_confidence=min_confidence, stats=match_stats)
    info["edges_matched"] = len(table); info.update(match_stats)
    if not table:
        return done(baseline, status="no_edge_match", note="Measurements were received but none matched a directed OSM edge (low confidence or geometry/direction mismatch); routes use modelled costs only.")
    result = optimizer.route_nodes(origin_node, destination_node, measured_by_edge=table, **kw)
    best = result["routes"][0]; share = best["measured_distance_share"]
    scope = (f"{share:.0%} of the best route's length is priced from measurements" if share > 0 else
             "the selected route contains no measured edges (measurements priced the corridor roads and steered the choice away from them)")
    stale = flow.data_state == STALE
    live_derived = flow.data_state == "live"  # cached / stale_cache provider data is applied but never reported as live
    label = {"live": "live", "cached": f"cached ({flow.age_seconds:.0f}s old)" if flow.age_seconds is not None else "cached", "stale_cache": "STALE cached (not live)"}.get(flow.data_state, flow.data_state)
    return done(result, status="applied", is_live=live_derived, coverage_best_route=share,
                note=(f"{provider.name.upper()} {label} flow speeds replaced the modelled congestion factor on matched directed edges only; {scope}. "
                      "All other edges use modelled costs."),
                closures_in_graph=sum(1 for r in table.values() if r["road_closure"]),
                baseline_best_time_min=baseline["routes"][0]["estimated_travel_time_min"],
                baseline_best_nodes=baseline["routes"][0]["node_sequence"])
