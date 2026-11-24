"""Coordinator: TomTom incident fetch -> road-graph matching -> IncidentManager registry.

* ``refresh()`` performs at most one provider request (the provider enforces min-interval / back-off), matches the
  returned incidents to directed OSM edges and reconciles the registry. A failed request leaves the registry untouched.
* Only incidents that are matched with enough confidence, confirmed by the provider (probability certain/probable) and
  currently present are applied to routing costs. All others stay visible in the feed with the reason.
* ``start_background()`` optionally polls on a daemon thread so routing sees fresh incidents even when no browser is open.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from routing.flow_matching import EdgeIndex
from routing.incident_manager import IncidentManager
from routing.incident_matching import (R_NO_GRAPH, REASON_TEXT, MatchConfig, MatchResult, match_incident)
from services.tomtom_incidents import FetchOutcome, TomTomIncidentService

log = logging.getLogger("trafficiq.incident_sync")

# graph loader -> (graph or None, graph source label, is real OpenStreetMap geometry)
GraphLoader = Callable[[], tuple[Any, str, bool]]


def _match_config_from_env() -> MatchConfig:
    def f(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, "").strip() or default)
        except ValueError:
            return default
    return MatchConfig(max_distance_m=f("TOMTOM_INCIDENTS_MATCH_MAX_DISTANCE_M", 30.0),
                       min_confidence=f("TOMTOM_INCIDENTS_MATCH_MIN_CONFIDENCE", 0.55),
                       direction_aware=os.getenv("TOMTOM_INCIDENTS_DIRECTION_AWARE", "true").strip().lower() not in {"0", "false", "no"})


class IncidentSyncService:
    def __init__(self, provider: TomTomIncidentService, manager: IncidentManager, graph_loader: GraphLoader,
                 match_config: MatchConfig | None = None, index_factory: Callable[[Any], EdgeIndex] | None = None):
        self.provider, self.manager, self._graph_loader = provider, manager, graph_loader
        self.match_config = match_config or _match_config_from_env()
        self._index_factory = index_factory
        self._lock = threading.RLock()
        self._match_cache: dict[tuple, MatchResult] = {}
        self._graph_id: int | None = None
        self._last_sync: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        manager.provider_stale_after_seconds = provider.config.stale_after_seconds

    # ------------------------------------------------------------------ matching
    def _index(self, graph):
        if self._index_factory:
            return self._index_factory(graph)
        from routing.tomtom_routing import edge_index_for  # reuses the process-wide directed-edge index cache
        return edge_index_for(graph)

    def _match_all(self, incidents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        graph, source, is_osm = self._graph_loader()
        info = {"graph_source": source, "graph_is_openstreetmap": bool(is_osm)}
        entries: list[dict[str, Any]] = []
        index = None
        if graph is not None and is_osm:
            index = self._index(graph)
            if self._graph_id != id(graph):
                self._match_cache.clear()
                self._graph_id = id(graph)
        live_ids = set()
        for inc in incidents:
            ck = (inc["provider_id"], str(inc["geometry"]), tuple(inc["road_numbers"]))
            live_ids.add(ck)
            if index is None:
                result = MatchResult("unmatched", 0.0, [], R_NO_GRAPH, REASON_TEXT[R_NO_GRAPH])
            else:
                result = self._match_cache.get(ck)
                if result is None:
                    try:
                        hints = [inc.get("from"), inc.get("to")]
                        result = match_incident(graph, index, inc["geometry"], road_numbers=inc["road_numbers"], name_hints=hints,
                                                config=self.match_config)
                    except Exception as exc:  # a matcher bug must never break the feed
                        log.exception("Incident matching failed for one incident")
                        result = MatchResult("unmatched", 0.0, [], "low_confidence", f"Matching error: {type(exc).__name__}")
                    self._match_cache[ck] = result
            applied, skip = self._routing_decision(inc, result)
            entries.append({"incident": inc, "match": result.to_public(), "routing_applied": applied, "routing_skip_reason": skip})
        for ck in [k for k in self._match_cache if k not in live_ids]:
            self._match_cache.pop(ck, None)
        return entries, info

    @staticmethod
    def _routing_decision(inc: dict[str, Any], match: MatchResult) -> tuple[bool, str | None]:
        if not match.matched:
            return False, "not matched to a road edge: " + (match.reason or "unknown")
        if inc.get("time_validity") == "future":
            return False, "planned/future incident (not active now)"
        if not inc.get("confirmed"):
            return False, f"provider probability is '{inc.get('probability_of_occurrence')}' (only certain/probable are applied)"
        return True, None

    # ------------------------------------------------------------------ public
    def refresh(self) -> dict[str, Any]:
        with self._lock:
            outcome: FetchOutcome = self.provider.refresh()
            sync: dict[str, Any] | None = None
            if outcome.fetched:
                entries, graph_info = self._match_all(outcome.incidents)
                stats = self.manager.sync_provider_incidents(entries, ttl_seconds=self.provider.config.stale_after_seconds * 2)
                sync = {**stats, **graph_info}
                self._last_sync = sync
            return {"refreshed": outcome.fetched, "throttled": outcome.throttled, "sync": sync or self._last_sync,
                    "provider_status": outcome.status, "counts": self.manager.counts()}

    def status(self) -> dict[str, Any]:
        s = self.provider.status()
        s["counts"] = self.manager.counts()
        s["last_sync"] = self._last_sync
        s["background_polling"] = bool(self._thread and self._thread.is_alive())
        return s

    # ------------------------------------------------------------------ background polling
    def start_background(self) -> bool:
        if not self.provider.active or (self._thread and self._thread.is_alive()):
            return False
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.refresh()
                except Exception:
                    log.exception("Background incident refresh failed")
                self._stop.wait(self.provider.config.poll_interval_seconds)

        self._thread = threading.Thread(target=loop, name="tomtom-incident-poller", daemon=True)
        self._thread.start()
        return True

    def stop_background(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
