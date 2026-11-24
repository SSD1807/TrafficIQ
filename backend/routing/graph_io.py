"""Dependency-light GraphML reader for the cached OpenStreetMap road graph.

``osmnx.load_graphml`` is the primary loader (OSMnx is a declared dependency). This module is a *real-data* fallback for
environments where OSMnx/Shapely cannot be imported: it reads the very same cached OSM GraphML file with NetworkX and
applies the same type conversions, so the graph is still genuine OpenStreetMap data (never a synthetic substitute).
Edge geometries (WKT ``LINESTRING``) are exposed through a tiny object with a ``.coords`` attribute, which is all the
routing and incident-matching code needs from a Shapely geometry.
"""
from __future__ import annotations

import re
from pathlib import Path

import networkx as nx

_FLOAT_NODE = ("x", "y", "lat", "lon")
_FLOAT_EDGE = ("length", "speed_kph", "travel_time", "grade", "grade_abs", "bearing")
_BOOL_EDGE = ("oneway", "reversed", "bridge", "tunnel")
_PAIR = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


class WKTLine:
    """Minimal stand-in for ``shapely.geometry.LineString`` (coords = [(lon, lat), ...])."""

    def __init__(self, coords: list[tuple[float, float]]) -> None:
        self.coords = coords

    def __repr__(self) -> str:  # pragma: no cover
        return f"WKTLine({len(self.coords)} pts)"


def parse_linestring(text: str) -> WKTLine | None:
    if not isinstance(text, str) or not text.upper().startswith("LINESTRING"):
        return None
    pts = [(float(a), float(b)) for a, b in _PAIR.findall(text)]
    return WKTLine(pts) if len(pts) >= 2 else None


def _to_bool(value) -> bool | list:
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("["):
            return [_to_bool(x.strip(" '\"")) for x in v.strip("[]").split(",") if x.strip()]
        return v.lower() in {"true", "yes", "1"}
    return bool(value)


def _maybe_list(value):
    """OSMnx stores merged attributes as the string repr of a list, e.g. "['a', 'b']"."""
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        items = [x.strip().strip("'\"") for x in value[1:-1].split(",") if x.strip()]
        return items
    return value


def _node_id(value):
    """OSMnx returns integer node ids; mirror that so both loaders give identical graphs."""
    text = str(value)
    return int(text) if text.lstrip("-").isdigit() else text


def load_graphml_networkx(path: str | Path) -> nx.MultiDiGraph:
    raw = nx.read_graphml(str(path))
    G = nx.MultiDiGraph()
    G.graph.update({k: v for k, v in raw.graph.items() if isinstance(v, (str, int, float, bool))})
    for node, data in raw.nodes(data=True):
        d = dict(data)
        for k in _FLOAT_NODE:
            if k in d:
                d[k] = float(d[k])
        G.add_node(_node_id(node), **d)
    for u, v, key, data in raw.edges(keys=True, data=True):
        d = dict(data)
        for k in _FLOAT_EDGE:
            if k in d:
                try:
                    d[k] = float(d[k])
                except (TypeError, ValueError):
                    d.pop(k, None)
        for k in _BOOL_EDGE:
            if k in d:
                d[k] = _to_bool(d[k])
        for k in ("name", "highway", "ref", "osmid", "maxspeed", "lanes"):
            if k in d:
                d[k] = _maybe_list(d[k])
        if "geometry" in d:
            geom = parse_linestring(d["geometry"])
            if geom is None:
                d.pop("geometry")
            else:
                d["geometry"] = geom
        try:
            ikey = int(key)
        except (TypeError, ValueError):
            ikey = 0
        G.add_edge(_node_id(u), _node_id(v), key=ikey, **d)
    G.graph["crs"] = G.graph.get("crs", "epsg:4326")
    return G
