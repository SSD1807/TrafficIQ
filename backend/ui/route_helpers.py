"""Route planning helpers shared by the map and route-planner pages."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import networkx as nx
import pandas as pd

from backend.dependencies import get_optimizer
from routing.osm_network import haversine_m
from ui.context import Ctx, hour_bucket, simulated_congestion
from ui.status import IST


def named_roads(ctx: Ctx) -> pd.DataFrame:
    """One row per distinct road name with a representative midpoint (for search)."""
    e = ctx.edges[ctx.edges.name != ""]
    if e.empty:
        return pd.DataFrame(columns=["name", "highway", "lat", "lon", "km"])
    rows = []
    for name, grp in e.groupby("name"):
        mid = grp.iloc[len(grp) // 2]
        pts = mid.coords[len(mid.coords) // 2]
        rows.append({"name": name, "highway": grp.highway.mode().iat[0], "lat": pts[1], "lon": pts[0],
                     "km": grp.length_m.sum() / 1000})
    return pd.DataFrame(rows).sort_values("km", ascending=False).reset_index(drop=True)


def nearest_node(ctx: Ctx, lat: float, lon: float):
    return get_optimizer().nearest_node(lat, lon)


def snap_distance_m(ctx: Ctx, node, lat: float, lon: float) -> float:
    a = ctx.graph.nodes[node]
    return haversine_m(lat, lon, float(a["y"]), float(a["x"]))


def compute_routes(ctx: Ctx, origin: tuple[float, float], dest: tuple[float, float], *, algorithm: str,
                   departure: datetime, rain_mm: float, use_sim: bool, alternatives: int,
                   use_tomtom: bool = False) -> dict[str, Any]:
    opt = get_optimizer()
    o, d = nearest_node(ctx, *origin), nearest_node(ctx, *dest)
    cong = simulated_congestion(ctx.graph_key, hour_bucket(departure), float(rain_mm), ctx.graph) if use_sim else None
    t0 = time.perf_counter()
    kwargs = dict(algorithm=algorithm, alternatives=alternatives, congestion_by_edge=cong, rainfall_mm=rain_mm)
    if use_tomtom:
        from routing.tomtom_routing import route_with_tomtom_flow
        res = route_with_tomtom_flow(opt, ctx.tomtom, o, d, **kwargs)
    else:
        res = opt.route_nodes(o, d, **kwargs)
    res["compute_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    res.setdefault("traffic_flow", {"status": "not_requested", "is_live": False})
    res.update(origin_latlon=origin, dest_latlon=dest, origin_snap_m=round(snap_distance_m(ctx, o, *origin), 1),
               dest_snap_m=round(snap_distance_m(ctx, d, *dest), 1), departure_time=departure.isoformat(),
               used_simulated_congestion=use_sim, rainfall_mm=rain_mm)
    if res["routes"]:
        res["estimated_arrival_time"] = (departure + timedelta(minutes=res["routes"][0]["estimated_travel_time_min"])).isoformat()
    return res


def compare_algorithms(ctx: Ctx, origin, dest, **kw) -> list[dict]:
    out = []
    for algo in ("dijkstra", "astar"):
        r = compute_routes(ctx, origin, dest, algorithm=algo, alternatives=1, **kw)
        out.append({"Algorithm": "Dijkstra" if algo == "dijkstra" else "A*", "Travel time (min)": r["routes"][0]["estimated_travel_time_min"],
                    "Distance (km)": r["routes"][0]["distance_km"], "Compute time (ms)": r["compute_ms"]})
    return out


def default_departure() -> datetime:
    return datetime.now(IST).replace(second=0, microsecond=0)
