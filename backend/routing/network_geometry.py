"""Road-network geometry for map display (real OSM edge shapes, de-duplicated, size-capped)."""
from __future__ import annotations

from typing import Any

from routing.labels import edge_road_name

MAJOR = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}


def _highway(attrs: dict[str, Any]) -> str:
    h = attrs.get("highway")
    h = h[0] if isinstance(h, (list, tuple)) and h else h
    return str(h or "unknown")


def road_segments(graph, *, bbox: tuple[float, float, float, float] | None = None, level: str = "major", limit: int = 6000) -> dict[str, Any]:
    """bbox = (min_lat, min_lon, max_lat, max_lon). Two-way roads are emitted once. ``truncated`` says when ``limit`` cut the result."""
    if level not in {"major", "all"}:
        raise ValueError("level must be 'major' or 'all'")
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    total = 0
    for u, v, attrs in graph.edges(data=True):
        hw = _highway(attrs)
        if level == "major" and hw not in MAJOR:
            continue
        pair = (str(u), str(v)) if str(u) <= str(v) else (str(v), str(u))
        if pair in seen:
            continue
        a, b = graph.nodes[u], graph.nodes[v]
        geom = attrs.get("geometry")
        pts = [[round(float(y), 5), round(float(x), 5)] for x, y in geom.coords] if geom is not None and hasattr(geom, "coords") \
            else [[round(float(a["y"]), 5), round(float(a["x"]), 5)], [round(float(b["y"]), 5), round(float(b["x"]), 5)]]
        if bbox and not any(bbox[0] <= p[0] <= bbox[2] and bbox[1] <= p[1] <= bbox[3] for p in pts):
            continue
        seen.add(pair)
        total += 1
        if len(out) < limit:
            out.append({"name": edge_road_name(attrs), "highway": hw, "positions": pts})
    return {"level": level, "count": len(out), "total_matching": total, "truncated": total > len(out), "segments": out}
