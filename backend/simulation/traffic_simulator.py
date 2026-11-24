"""Deterministic scenario traffic simulation for edge-level route-cost stress tests."""
from __future__ import annotations

from datetime import datetime
import hashlib

import networkx as nx


class TrafficSimulator:
    def __init__(self, seed: int = 42):
        self.seed = int(seed)

    def _stable_noise(self, key: str, time_key: str) -> float:
        digest = hashlib.sha256(f"{self.seed}|{key}|{time_key}".encode()).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF

    def edge_conditions(self, graph: nx.Graph, when: datetime | None = None,
                        *, rainfall_mm: float = 0.0, spike_edges: set[tuple] | None = None) -> dict:
        when = when or datetime.now()
        hour = when.hour
        weekend = when.weekday() >= 5
        rush = (7 <= hour <= 10) or (17 <= hour <= 20)
        base = 0.25 if not rush else 0.70
        if weekend: base *= 0.82
        if hour >= 22 or hour <= 5: base *= 0.55
        if rainfall_mm >= 5: base = min(0.95, base + 0.12)
        spike_edges = spike_edges or set()
        time_key = when.strftime("%Y%m%d%H%M")
        levels = {}
        for u, v, data in graph.edges(data=True):
            highway = data.get("highway", "residential")
            if isinstance(highway, list): highway = highway[0] if highway else "residential"
            road_factor = {"motorway": 0.70, "trunk": 0.68, "primary": 0.95,
                           "secondary": 0.88, "tertiary": 0.75, "residential": 0.55,
                           "service": 0.45}.get(str(highway), 0.65)
            noise = self._stable_noise(f"{u}->{v}", time_key) * 0.22
            load = min(1.0, max(0.0, base * road_factor + noise))
            if (u, v) in spike_edges:
                load = min(1.0, load + 0.35)
            level = "high" if load >= 0.72 else "medium" if load >= 0.43 else "low"
            levels[(u, v)] = level
        return levels
