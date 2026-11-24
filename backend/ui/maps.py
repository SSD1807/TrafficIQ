"""Folium map construction on real OSM geometry (or the labelled synthetic fallback).

Roads are drawn from the graph's own edge geometry (OSMnx). Colours come from either
the OSM road class or a *simulated* congestion overlay -- never from live traffic.
"""
from __future__ import annotations

import json
import math
from typing import Any

import folium
import networkx as nx
import pandas as pd
from folium import plugins

from ui.theme import CONGESTION_COLORS

ROAD_COLORS = {
    "motorway": "#7c3aed", "trunk": "#9333ea", "primary": "#2563eb", "secondary": "#0ea5e9",
    "tertiary": "#14b8a6", "unclassified": "#94a3b8", "residential": "#94a3b8",
    "living_street": "#cbd5e1", "service": "#cbd5e1",
}
ROAD_WEIGHT = {"motorway": 5, "trunk": 5, "primary": 4.5, "secondary": 3.5, "tertiary": 3, "residential": 2, "service": 1.5}
MAJOR = ["motorway", "trunk", "primary", "secondary", "tertiary"]
ALL_CLASSES = MAJOR + ["unclassified", "residential", "living_street", "service"]
INCIDENT_STYLE = {"accident": ("red", "exclamation-sign"), "road_closure": ("black", "remove-sign"),
                  "road_work": ("orange", "wrench"), "obstruction": ("orange", "ban-circle"), "hazard": ("purple", "warning-sign")}
_CARTO_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
TILES = {
    "Light": ("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", _CARTO_ATTR),
    "Dark": ("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", _CARTO_ATTR),
    "OpenStreetMap": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'),
}
ROUTE_COLORS = ["#16a34a", "#2563eb", "#9333ea", "#ea580c", "#0f766e"]


def _first(v: Any, default: Any = None):
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v if v is not None else default


def edges_frame(graph: nx.MultiDiGraph) -> pd.DataFrame:
    """Flatten graph edges to plain rows with coordinates (works for OSM and fallback graphs)."""
    rows = []
    for u, v, k, d in graph.edges(keys=True, data=True):
        geom = d.get("geometry")
        if geom is not None and hasattr(geom, "coords"):
            coords = [(float(x), float(y)) for x, y in geom.coords]
        else:
            a, b = graph.nodes[u], graph.nodes[v]
            coords = [(float(a["x"]), float(a["y"])), (float(b["x"]), float(b["y"]))]
        hw = str(_first(d.get("highway"), "unclassified"))
        name = _first(d.get("name"))
        oneway = d.get("oneway")
        oneway = oneway.strip().lower() in {"true", "yes", "1"} if isinstance(oneway, str) else bool(oneway)
        rows.append({"u": u, "v": v, "key": k, "name": str(name) if name else "", "highway": hw,
                     "oneway": oneway, "length_m": float(d.get("length", 0) or 0),
                     "speed_kph": float(_first(d.get("speed_kph"), 0) or 0), "coords": coords})
    return pd.DataFrame(rows)


def base_map(center: tuple[float, float], zoom: int, tile: str = "Light") -> folium.Map:
    fmap = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True, prefer_canvas=True)
    for label, (url, attr) in TILES.items():
        folium.TileLayer(tiles=url, attr=attr, name=f"Basemap: {label}", show=(label == tile), max_zoom=19).add_to(fmap)
    plugins.Fullscreen(position="topleft").add_to(fmap)
    plugins.LocateControl(position="topleft", strings={"title": "Show my location"}, flyTo=True).add_to(fmap)
    return fmap


_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}


def add_roads(fmap: folium.Map, edges: pd.DataFrame, *, classes: list[str], mode: str,
              congestion: dict | None = None, max_edges: int = 25000) -> int:
    """Draw roads as a few grouped GeoJSON layers (compact output, fast in the browser).

    mode: 'Road type' or 'Simulated congestion'. Two-way streets are drawn once
    (worst simulated level of the two directions). Returns the number of drawn segments.
    """
    sub = edges[edges.highway.isin(classes)]
    if sub.empty:
        return 0
    one = sub[sub.oneway]
    two = sub[~sub.oneway].copy()
    if not two.empty:  # collapse u->v / v->u duplicates
        two["_k"] = [(min(str(a), str(b)), max(str(a), str(b)), round(l)) for a, b, l in zip(two.u, two.v, two.length_m)]
        two = two.drop_duplicates("_k")
    sub = pd.concat([one, two.drop(columns="_k", errors="ignore")])
    if len(sub) > max_edges:  # keep the browser responsive; majors first
        order = {c: i for i, c in enumerate(ALL_CLASSES)}
        sub = sub.assign(_o=sub.highway.map(order).fillna(99)).sort_values("_o").head(max_edges)

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in sub.itertuples():
        sim = ""
        if mode == "Simulated congestion" and congestion is not None:
            lv = congestion.get((r.u, r.v), "low")
            if not r.oneway:
                back = congestion.get((r.v, r.u), "low")
                lv = lv if _LEVEL_RANK.get(str(lv), 0) >= _LEVEL_RANK.get(str(back), 0) else back
            sim = str(lv).capitalize(); color = CONGESTION_COLORS.get(sim, "#94a3b8")
        else:
            color = ROAD_COLORS.get(r.highway, "#94a3b8")
        coords = []
        for x, y in r.coords:
            pt = [round(x, 5), round(y, 5)]
            if not coords or coords[-1] != pt:
                coords.append(pt)
        if len(coords) < 2:
            continue
        feat = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"name": r.name or "Unnamed road", "type": r.highway, "dir": "One-way" if r.oneway else "Two-way",
                               "len": f"{r.length_m:.0f} m", "sim": sim or "–"}}
        groups.setdefault((r.highway, color), []).append(feat)

    fields = ["name", "type", "dir", "len"] + (["sim"] if mode == "Simulated congestion" else [])
    aliases = ["Road", "Type", "Direction", "Length"] + (["Simulated congestion"] if mode == "Simulated congestion" else [])
    rank = {c: i for i, c in enumerate(ALL_CLASSES)}
    parent = folium.FeatureGroup(name="Road network (OpenStreetMap geometry)", show=True)
    for (hw, color), feats in sorted(groups.items(), key=lambda kv: -rank.get(kv[0][0], 99)):  # minor roads first, majors on top
        folium.GeoJson({"type": "FeatureCollection", "features": feats},
                       style_function=lambda _f, c=color, w=ROAD_WEIGHT.get(hw, 2): {"color": c, "weight": w, "opacity": 0.85},
                       tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=True, localize=False)).add_to(parent)
    parent.add_to(fmap)

    # One-way direction arrows: small rotated markers at segment midpoints. Deliberately NOT a Leaflet
    # text-path plugin: that needs an SVG renderer and throws under the canvas renderer used here,
    # which aborted the whole map script on real one-way data.
    ow = sub[(sub.oneway) & (sub.length_m > 120)].head(200)
    if not ow.empty:
        arrows = folium.FeatureGroup(name="One-way direction arrows", show=True)
        for r in ow.itertuples():
            pts = r.coords; i = max(1, len(pts) // 2)
            (x1, y1), (x2, y2) = pts[i - 1], pts[i]
            dx = (x2 - x1) * math.cos(math.radians((y1 + y2) / 2)); dy = y2 - y1
            bearing = math.degrees(math.atan2(dx, dy)) % 360
            folium.Marker(((y1 + y2) / 2, (x1 + x2) / 2), icon=folium.DivIcon(
                icon_size=(14, 14), icon_anchor=(7, 7), class_name="oneway-arrow",
                html=f'<div style="font-size:13px;line-height:14px;text-align:center;color:#0f172a;'
                     f'text-shadow:0 0 3px #fff,0 0 3px #fff;transform:rotate({bearing:.0f}deg)">&#9650;</div>'),
                interactive=False).add_to(arrows)
        arrows.add_to(fmap)
    return int(len(sub))


def add_routes(fmap: folium.Map, routes: list[dict]) -> None:
    group = folium.FeatureGroup(name="Routes", show=True)
    for i, r in reversed(list(enumerate(routes))):
        pts = r.get("polyline_latlon") or []
        if not pts:
            continue
        label = f"{'Best route' if i == 0 else f'Alternative {i}'} · {r['estimated_travel_time_min']:.1f} min · {r['distance_km']:.2f} km"
        if r.get("via_roads"):
            label += "<br>via " + ", ".join(r["via_roads"])
        folium.PolyLine(pts, color="#ffffff", weight=11 if i == 0 else 8, opacity=0.9).add_to(group)
        folium.PolyLine(pts, color=ROUTE_COLORS[i % len(ROUTE_COLORS)], weight=7 if i == 0 else 4.5,
                        opacity=1 if i == 0 else 0.85, dash_array=None if i == 0 else "8 6",
                        tooltip=folium.Tooltip(label, sticky=True)).add_to(group)
    group.add_to(fmap)


def add_endpoints(fmap: folium.Map, origin: tuple[float, float] | None, dest: tuple[float, float] | None) -> None:
    if origin:
        folium.Marker(origin, tooltip="Origin", icon=folium.Icon(color="green", icon="play", prefix="glyphicon")).add_to(fmap)
    if dest:
        folium.Marker(dest, tooltip="Destination", icon=folium.Icon(color="red", icon="flag", prefix="glyphicon")).add_to(fmap)


def edge_midpoint(graph: nx.MultiDiGraph, u, v) -> tuple[float, float] | None:
    data = graph.get_edge_data(u, v)
    if not data:
        return None
    d = next(iter(data.values()))
    geom = d.get("geometry")
    if geom is not None and hasattr(geom, "interpolate"):
        p = geom.interpolate(0.5, normalized=True); return (p.y, p.x)
    a, b = graph.nodes[u], graph.nodes[v]
    return ((a["y"] + b["y"]) / 2, (a["x"] + b["x"]) / 2)


def add_incidents(fmap: folium.Map, graph: nx.MultiDiGraph, incidents: list[dict]) -> int:
    group = folium.FeatureGroup(name="Incidents & closures", show=True)
    n = 0
    by_key = {str(k): k for k in graph.nodes}
    for inc in incidents:
        u, v = by_key.get(inc["edge_u"]), by_key.get(inc["edge_v"])
        if u is None or v is None:
            continue
        mid = edge_midpoint(graph, u, v)
        if not mid:
            continue
        color, icon = INCIDENT_STYLE.get(inc["kind"], ("gray", "info-sign"))
        label = (f"<b>{inc['kind'].replace('_', ' ').title()}</b> (severity {inc['severity']:.2f})<br>"
                 f"Source: {inc['source']}<br>Expires: {inc['expires_at'][:19]} UTC<br>{inc.get('description', '')}")
        folium.Marker(mid, tooltip=folium.Tooltip(label), icon=folium.Icon(color=color, icon=icon, prefix="glyphicon")).add_to(group)
        if inc["kind"] == "road_closure":  # draw the closed segment itself
            d = next(iter(graph.get_edge_data(u, v).values())); geom = d.get("geometry")
            pts = [(y, x) for x, y in geom.coords] if geom is not None and hasattr(geom, "coords") else \
                  [(graph.nodes[u]["y"], graph.nodes[u]["x"]), (graph.nodes[v]["y"], graph.nodes[v]["x"])]
            folium.PolyLine(pts, color="#000000", weight=6, dash_array="4 8", opacity=0.9).add_to(group)
        n += 1
    group.add_to(fmap)
    return n


def add_locations(fmap: folium.Map, rows: list[dict]) -> None:
    """Monitored locations coloured by the ML prediction (synthetic-data model)."""
    group = folium.FeatureGroup(name="Monitored locations (ML prediction, synthetic data)", show=True)
    for r in rows:
        color = CONGESTION_COLORS.get(r.get("predicted"), "#64748b")
        tip = (f"<b>{r['location']}</b><br>Predicted next 30 min: <b>{r.get('predicted', 'n/a')}</b>"
               + (f" ({r['confidence']:.0%})" if r.get("confidence") is not None else "")
               + f"<br><i>{r.get('area', '')}</i><br>Synthetic data · approximate anchor")
        folium.CircleMarker((r["lat"], r["lon"]), radius=11, color="#ffffff", weight=2, fill=True,
                            fill_color=color, fill_opacity=0.95, tooltip=folium.Tooltip(tip)).add_to(group)
    group.add_to(fmap)


def add_legend(fmap: folium.Map, mode: str, dark: bool = False, *, markers: bool = True, compact: bool = False) -> None:
    bg, fg = ("#161b26", "#e6eaf2") if dark else ("#ffffff", "#0f172a")
    max_w = "170px" if compact else "230px"
    def dot(c, t): return f'<div><span style="display:inline-block;width:18px;height:4px;background:{c};margin-right:8px;vertical-align:middle"></span>{t}</div>'
    def circ(c, t): return f'<div><span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{c};margin-right:8px;vertical-align:middle"></span>{t}</div>'
    if mode == "Simulated congestion":
        roads = "<b>Roads · simulated congestion</b>" + "".join(dot(CONGESTION_COLORS[k], k) for k in ("Low", "Medium", "High"))
    else:
        roads = "<b>Roads · OSM type</b>" + "".join(dot(ROAD_COLORS[k], k.title()) for k in ("motorway", "primary", "secondary", "tertiary")) + dot("#94a3b8", "Residential / other")
    extra = "<hr style='margin:6px 0'>"
    if markers:
        extra += "<b>Locations (ML prediction)</b>" + circ("#16a34a", "Predicted Low") + circ("#d97706", "Predicted Medium") + circ("#dc2626", "Predicted High")
    extra += "<b>Routes &amp; incidents</b>" + dot(ROUTE_COLORS[0], "Best route (solid)") + dot(ROUTE_COLORS[1], "Alternatives (dashed)") + dot("#000", "Closed segment") + "<div>&#9650; one-way direction (arrow points the way)</div>"
    if compact:  # small maps: keep only what the route view needs
        roads = "<b>Roads</b>" + dot(ROAD_COLORS["primary"], "Major") + dot("#94a3b8", "Other")
        extra = "<hr style='margin:4px 0'>" + dot(ROUTE_COLORS[0], "Best route") + dot(ROUTE_COLORS[1], "Alternatives (dashed)") + dot("#000", "Closed segment")
    html = (f'<div style="position:fixed;bottom:28px;left:12px;z-index:9999;background:{bg};color:{fg};padding:10px 12px;'
            f'border-radius:10px;border:1px solid rgba(128,128,128,.35);font:12px/1.5 sans-serif;max-width:{max_w};'
            f'box-shadow:0 2px 8px rgba(0,0,0,.25)">{roads}{extra}</div>')
    fmap.get_root().html.add_child(folium.Element(html))


def graph_bounds(edges: pd.DataFrame) -> tuple[tuple[float, float], list[list[float]]]:
    xs = [c[0] for cs in edges.coords for c in cs]; ys = [c[1] for cs in edges.coords for c in cs]
    return ((min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2), [[min(ys), min(xs)], [max(ys), max(xs)]]
