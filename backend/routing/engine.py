"""Shared incident-aware routing engine (used by /routes/plan, /incidents/analysis and the Copilot).

Pipeline for one request
------------------------
1. NORMAL route   : Dijkstra / A* on the road graph with NO incident effects (free-flow + optional congestion/weather).
2. INCIDENTS      : the unified feed of the single ``IncidentManager`` (TomTom live, manual, simulated). Each incident was
                    matched to OSM edges by ``routing.incident_matching`` and priced by ``routing.impact_rules``.
3. ANALYSIS       : which incidents lie on the normal route, how much of it they cover, whether a confirmed closure blocks it.
4. PRICED graph   : closures remove edges, other incidents add delay/cost (``RouteOptimizer._routing_view``).
5. ALTERNATIVES   : Dijkstra / A* (same algorithm as selected) plus k-shortest paths on the priced graph.
6. DECISION       : reroute when blocked, or when the priced alternative saves at least the configured time/percentage.

Every number is a model estimate built from OSM road attributes and the provider fields that were actually reported;
nothing is invented, and the response says which data sources were live, cached, unavailable or user-created.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

import networkx as nx

from routing.impact_rules import ImpactRules, get_rules
from routing.labels import LocationLabeler, edge_road_name
from routing.route_optimizer import RouteOptimizer

LOG = logging.getLogger("trafficiq.engine")
LIVE_STATES = {"live", "live_empty", "cached"}


def feed_id(record: dict[str, Any]) -> str:
    return f"tomtom:{record['provider_id']}" if record.get("provider_id") else record["id"]


def _edge_len(graph, u, v) -> float:
    data = graph.get_edge_data(u, v) or {}
    if not data:
        return 0.0
    items = data.values() if isinstance(graph, (nx.MultiDiGraph, nx.MultiGraph)) else [data]
    return min(float(d.get("length", 0.0) or 0.0) for d in items)


def _pairs(path: list) -> list[tuple[str, str]]:
    seq = [str(n) for n in path]
    return list(zip(seq[:-1], seq[1:]))


class IncidentRoutingEngine:
    def __init__(self, graph, graph_source: str, manager, *, rules: ImpactRules | None = None, labeler: LocationLabeler | None = None) -> None:
        self.graph, self.graph_source, self.manager = graph, graph_source, manager
        self.rules = rules or get_rules()
        self.labeler = labeler or LocationLabeler(graph)
        self._str_index: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------ helpers
    def _optimizer(self, manager) -> RouteOptimizer:
        opt = RouteOptimizer(self.graph, self.graph_source, manager)
        opt.impact_rules = self.rules
        return opt

    def _effects(self, excluded: set[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        return self.manager.edge_effects(excluded) if self.manager is not None else {}

    def incidents_on_path(self, path: list, excluded: set[str]) -> dict[str, dict[str, Any]]:
        """feed id -> {length_m, blocked, delay_s, edges} for incidents whose priced edges the path traverses."""
        effects = self._effects(excluded)
        hits: dict[str, dict[str, Any]] = {}
        for u, v in _pairs(path):
            for rec in effects.get((u, v), []):
                eff = self.rules.edge_effect(rec)
                h = hits.setdefault(feed_id(rec), {"length_m": 0.0, "blocked": False, "delay_s": 0.0, "edges": []})
                h["length_m"] += _edge_len(self.graph, u, v)
                h["blocked"] |= eff["effect"] == "block"
                h["delay_s"] += eff["delay_s"]
                h["edges"].append([u, v])
        return hits

    def _priced_cost_s(self, view: nx.DiGraph, path: list) -> float:
        total = 0.0
        for u, v in _pairs(path):
            u2, v2 = self._node(view, u), self._node(view, v)
            if not view.has_edge(u2, v2):
                return float("inf")
            total += float(view[u2][v2]["travel_time_sec"])
        return total

    def _node(self, graph, key):
        """Map a node id (route output uses strings, OSMnx graphs use ints) back to the graph's own node object."""
        if key in graph:
            return key
        index = self._str_index.get(id(graph))
        if index is None:
            index = self._str_index[id(graph)] = {str(n): n for n in graph.nodes}
        return index.get(str(key), key)

    def _incident_location(self, entry: dict[str, Any]) -> dict[str, Any]:
        names = [e.get("road_name") for e in entry.get("edges", []) if e.get("road_name")]
        road = names[0] if names else None
        label_source = "osm_matched_edge" if road else None
        lat, lon = entry.get("latitude"), entry.get("longitude")
        if road is None and lat is not None and lon is not None:
            near = self.labeler.nearest_named_road(float(lat), float(lon))
            if near:
                road, label_source = near[0], "nearest_named_road"
        provider = entry.get("provider") or {}
        text = " to ".join(x for x in (provider.get("from"), provider.get("to")) if x) or None
        if road is None and entry.get("edges"):
            e0 = entry["edges"][0]
            try:
                road = self.labeler.label_node(self._node(self.graph, e0["u"]))["label"]
                label_source = "junction_label"
            except Exception:
                road = None
        return {"road_name": road, "road_name_source": label_source, "provider_from_to": text,
                "location_label": (f"Near {road}" if label_source == "nearest_named_road" else road) or text or (f"{float(lat):.5f}, {float(lon):.5f}" if lat is not None and lon is not None else "Unknown location")}

    # ------------------------------------------------------------------ incident analysis (shared with Incident Management)
    def analyse_feed(self, *, excluded: Iterable[str] = (), on_original: dict | None = None, on_recommended: dict | None = None,
                     on_alternatives: dict[str, dict] | None = None) -> list[dict[str, Any]]:
        excluded = set(excluded)
        out: list[dict[str, Any]] = []
        for entry in (self.manager.feed() if self.manager is not None else []):
            if entry["source"] in excluded:
                continue
            provider = entry.get("provider") or {}
            matched_len = sum(float(e.get("length_m") or 0.0) for e in entry.get("edges", []))
            incident_view = {"kind": entry["kind"], "severity": entry["severity"], "confirmed": entry.get("confirmed", True),
                             "category": provider.get("category"), "delay_seconds": provider.get("delay_seconds"),
                             "severity_source": entry.get("severity_source")}
            analysis = self.rules.explain(incident_view, affected_m=float(provider.get("length_m") or matched_len or 0.0))
            if entry["source"] == "tomtom" and not entry.get("routing_applied"):
                analysis["routing_effect"] = "none"
                analysis["explanation"] += f" Not applied to routing: {entry.get('routing_skip_reason') or entry['status']}."
            loc = self._incident_location(entry)
            row = {
                "id": entry["id"], "source": entry["source"], "kind": entry["kind"], "category": provider.get("category"),
                "description": entry["description"], "severity": entry["severity"], "severity_source": entry.get("severity_source"),
                "magnitude_label": provider.get("magnitude_label"), "status": entry["status"], "matched": entry["matched"],
                "routing_applied": entry["routing_applied"], "unmatched_reason": (entry.get("match") or {}).get("reason_text") if not entry["matched"] else None,
                "match_confidence": (entry.get("match") or {}).get("confidence"), "confirmed": entry.get("confirmed"),
                "stale": entry.get("stale"), "start_time": entry.get("start_time"), "end_time": entry.get("end_time"),
                "last_report_time": provider.get("last_report_time"), "last_seen_at": entry.get("last_seen_at"),
                "provider_length_m": provider.get("length_m"), "provider_delay_s": provider.get("delay_seconds"),
                "matched_length_m": round(matched_len, 1), "latitude": entry.get("latitude"), "longitude": entry.get("longitude"),
                "geometry": entry.get("geometry"), "edges": entry.get("edges", []),
                "blocked_lanes": None,          # TomTom Incident Details does not report lane counts: never assumed
                **loc, **analysis,
            }
            if on_original is not None:
                row["affects_original_route"] = entry["id"] in on_original
                row["affected_length_on_original_m"] = round(on_original.get(entry["id"], {}).get("length_m", 0.0), 1)
            if on_recommended is not None:
                row["affects_recommended_route"] = entry["id"] in on_recommended
            if on_alternatives is not None:
                row["on_alternative_routes"] = [rid for rid, hit in on_alternatives.items() if entry["id"] in hit]
            out.append(row)
        return out

    # ------------------------------------------------------------------ main entry point
    def plan(self, start, end, *, algorithm: str = "dijkstra", alternatives: int = 3, congestion_by_edge: dict | None = None,
             rainfall_mm: float = 0.0, exclude_sources: Iterable[str] = ()) -> dict[str, Any]:
        excluded = set(exclude_sources)
        kw = dict(algorithm=algorithm, congestion_by_edge=congestion_by_edge, rainfall_mm=rainfall_mm)
        algo = "astar" if algorithm.lower().replace("_", "") == "astar" else "dijkstra"

        # 1. normal route: no incident effects at all
        free = self._optimizer(None)
        normal = free.route_nodes(start, end, alternatives=1, **kw)["routes"][0]
        normal_path = [self._node(self.graph, n) for n in normal["node_sequence"]]
        on_original = self.incidents_on_path(normal_path, excluded)

        # 2-5. incident-priced graph, same algorithm, k alternatives
        priced_opt = self._optimizer(self.manager.view(excluded) if (self.manager is not None and excluded) else self.manager)
        view = priced_opt._routing_view(congestion_by_edge, rainfall_mm)
        priced_routes: list[dict[str, Any]] = []
        no_path_reason = None
        try:
            priced = priced_opt.route_nodes(start, end, alternatives=max(2, min(int(alternatives), 5)), prebuilt_view=view, **kw)
            priced_routes = priced["routes"]
        except nx.NetworkXNoPath:
            no_path_reason = "No drivable route remains once confirmed closures are removed from the road network."

        original_cost = self._priced_cost_s(view, normal_path)
        blocked = original_cost == float("inf") or any(h["blocked"] for h in on_original.values())

        # 6. decision
        decision: dict[str, Any]
        thresholds = {"min_saving_s": self.rules.reroute_min_saving_s, "min_saving_pct": self.rules.reroute_min_saving_pct}
        best = priced_routes[0] if priced_routes else None
        best_path = [self._node(self.graph, n) for n in best["node_sequence"]] if best else None
        same_path = bool(best and [str(n) for n in best_path] == [str(n) for n in normal_path])
        if best is None:
            recommended, action = None, "no_route"
            decision = {"action": action, "reason": no_path_reason or "No route found."}
        elif blocked and not same_path:
            recommended, action = best, "reroute"
            blockers = [i for i, h in on_original.items() if h["blocked"]]
            decision = {"action": action, "reason": "The normal route passes through a confirmed road closure; an alternative that avoids it was calculated.",
                        "triggering_incidents": blockers}
        else:
            saving = original_cost - float(best["estimated_travel_time_min"]) * 60.0
            pct = 100.0 * saving / original_cost if original_cost and original_cost != float("inf") else 0.0
            if (not same_path) and saving >= thresholds["min_saving_s"] and pct >= thresholds["min_saving_pct"]:
                recommended, action = best, "reroute"
                decision = {"action": action, "triggering_incidents": list(on_original),
                            "reason": f"Incidents on the normal route add an estimated {round(original_cost / 60 - normal['estimated_travel_time_min'], 1)} min; "
                                      f"the alternative is estimated {round(saving / 60, 1)} min ({round(pct, 1)}%) faster, above the reroute threshold."}
            else:
                recommended, action = priced_opt._path_result(view, [self._node(view, n) for n in normal_path], algo, 1), "keep_original"
                if on_original:
                    decision = {"action": action, "triggering_incidents": list(on_original),
                                "reason": ("Incidents lie on this route but none closes it, and no alternative is faster by the reroute threshold "
                                           f"({int(thresholds['min_saving_s'])} s and {thresholds['min_saving_pct']}%). The route is kept; see the warnings.")}
                else:
                    decision = {"action": action, "triggering_incidents": [], "reason": "No active incident affects the normal route, so it was not changed."}
        decision["thresholds"] = thresholds

        rec_path = [self._node(self.graph, n) for n in recommended["node_sequence"]] if recommended else []
        on_rec = self.incidents_on_path(rec_path, excluded) if recommended else {}
        alt_hits: dict[str, dict] = {}
        alt_routes = []
        for r in priced_routes:
            if recommended and r["node_sequence"] == recommended["node_sequence"]:
                continue
            rid = f"alt-{len(alt_routes) + 1}"
            alt_hits[rid] = self.incidents_on_path(r["node_sequence"], excluded)
            alt_routes.append({**r, "route_id": rid, "incident_ids": list(alt_hits[rid]), "incident_count": len(alt_hits[rid])})

        analysis = self.analyse_feed(excluded=excluded, on_original=on_original, on_recommended=on_rec, on_alternatives=alt_hits)
        avoided = [i for i in on_original if i not in on_rec]
        orig_out = {**normal, "route_id": "original", "role": "original", "incident_ids": list(on_original), "incident_count": len(on_original),
                    "blocked_by_closure": blocked, "time_with_incidents_min": None if original_cost == float("inf") else round(original_cost / 60, 2),
                    "free_flow_time_min": normal["estimated_travel_time_min"]}
        comparison = None
        if recommended:
            rec_cost_min = float(recommended["estimated_travel_time_min"])
            comparison = {
                "distance_delta_km": round(recommended["distance_km"] - normal["distance_km"], 3),
                "time_delta_vs_original_free_flow_min": round(rec_cost_min - normal["estimated_travel_time_min"], 2),
                "time_delta_vs_original_with_incidents_min": None if original_cost == float("inf") else round(rec_cost_min - original_cost / 60, 2),
                "incidents_avoided": avoided, "incidents_still_on_recommended": list(on_rec),
                "original_is_blocked": blocked,
            }
        rec_out = None
        if recommended:
            rec_out = {**recommended, "route_id": "recommended", "role": "recommended" if action == "reroute" else "original_kept",
                       "incident_ids": list(on_rec), "incident_count": len(on_rec), "is_original_route": action != "reroute"}
        return {"algorithm": algo, "graph_source": self.graph_source, "decision": decision, "original_route": orig_out,
                "recommended_route": rec_out, "alternative_routes": alt_routes, "comparison": comparison,
                "incident_analysis": analysis, "impact_rules": self.rules.to_public()}
