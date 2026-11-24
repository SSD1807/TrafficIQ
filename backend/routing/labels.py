"""Human-readable location labels for OSM nodes and edges.

Routing keeps using the original OSM node IDs. This module only produces display text, and only from real data:

0. ``osm_node_name``      - the node's own ``name`` attribute (a named junction/place), when it has one.
1. ``osm_edge_name``      - name(s) of the OSM road(s) that meet at the node (junction of A and B).
2. ``nearest_named_road`` - the closest named OSM road within ``MAX_NEAREST_M`` (labelled "Near ...").
3. ``reverse_geocode``    - Nominatim reverse lookup (road + locality) when it is cached/available (optional, rate limited).
4. ``coordinates``        - "Unnamed road junction (lat, lon)" - the node id is carried separately and is only a last resort.

A name is never invented: roads without an OSM ``name`` stay unnamed unless one of the sources above supplies it.
"""
from __future__ import annotations

import math
from typing import Any

from routing.osm_network import haversine_m

MAX_NEAREST_M = 150.0
CELL_DEG = 0.002


def names_of(attrs: dict[str, Any]) -> list[str]:
    raw = attrs.get("name")
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    return [str(n).strip() for n in items if isinstance(n, str) and n.strip()]


def edge_road_name(attrs: dict[str, Any]) -> str | None:
    names = names_of(attrs)
    return names[0] if names else None


def edge_label(attrs: dict[str, Any]) -> str:
    name = edge_road_name(attrs)
    if name:
        return name
    h = attrs.get("highway")
    h = h[0] if isinstance(h, (list, tuple)) and h else h
    return f"Unnamed {str(h or 'road').replace('_', ' ')}" + ("" if str(h or "").endswith("road") else " road")


class LocationLabeler:
    def __init__(self, graph, geocoder=None) -> None:
        self.graph = graph
        self.geocoder = geocoder
        self._grid: dict[tuple[int, int], list[tuple[float, float, str]]] | None = None

    # ------------------------------------------------------------------ helpers
    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG))

    def _named_grid(self):
        if self._grid is None:
            grid: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
            for u, v, d in self.graph.edges(data=True):
                ns = names_of(d)
                if not ns:
                    continue
                a, b = self.graph.nodes[u], self.graph.nodes[v]
                for lat, lon in ((float(a["y"]), float(a["x"])), ((float(a["y"]) + float(b["y"])) / 2, (float(a["x"]) + float(b["x"])) / 2)):
                    grid.setdefault(self._cell(lat, lon), []).append((lat, lon, ns[0]))
            self._grid = grid
        return self._grid

    def node_road_names(self, node) -> list[str]:
        out: list[str] = []
        for _, _, d in list(self.graph.out_edges(node, data=True)) + list(self.graph.in_edges(node, data=True)):
            for n in names_of(d):
                if n not in out:
                    out.append(n)
        return out

    def nearest_named_road(self, lat: float, lon: float, max_m: float = MAX_NEAREST_M) -> tuple[str, float] | None:
        grid, (cy, cx) = self._named_grid(), self._cell(lat, lon)
        best: tuple[str, float] | None = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for plat, plon, name in grid.get((cy + dy, cx + dx), ()):
                    d = haversine_m(lat, lon, plat, plon)
                    if d <= max_m and (best is None or d < best[1]):
                        best = (name, d)
        return best

    # ------------------------------------------------------------------ public
    def label_node(self, node, *, reverse: bool = False) -> dict[str, Any]:
        attrs = self.graph.nodes[node]
        lat, lon = float(attrs["y"]), float(attrs["x"])
        out: dict[str, Any] = {"id": str(node), "latitude": lat, "longitude": lon, "locality": None}
        peek = self.geocoder.peek_reverse(lat, lon) if self.geocoder else None
        if reverse and self.geocoder and not peek:
            peek = self.geocoder.reverse(lat, lon)
        if peek:
            out["locality"] = peek.get("area")
        own = attrs.get("name")
        names = self.node_road_names(node)
        if isinstance(own, str) and own.strip():
            out.update(label=own.strip(), road_names=names, label_source="osm_node_name")
        elif names:
            label = " / ".join(names[:2]) + (" junction" if len(names) > 1 else "")
            out.update(label=label, road_names=names, label_source="osm_edge_name")
        else:
            near = self.nearest_named_road(lat, lon)
            if near:
                out.update(label=f"Near {near[0]}", road_names=[near[0]], label_source="nearest_named_road", nearest_road_distance_m=round(near[1]))
            elif peek and (peek.get("road") or peek.get("area")):
                out.update(label=peek.get("road") or peek["area"], road_names=[peek["road"]] if peek.get("road") else [], label_source="reverse_geocode")
            else:
                out.update(label=f"Unnamed road junction ({lat:.5f}, {lon:.5f})", road_names=[], label_source="coordinates")
        if out["locality"] and out["locality"].lower() not in out["label"].lower():
            out["label"] = f"{out['label']}, {out['locality']}"
        if out["label_source"] not in ("coordinates", "osm_node_name") and "bhubaneswar" not in out["label"].lower():
            out["label"] += ", Bhubaneswar"
        return out
