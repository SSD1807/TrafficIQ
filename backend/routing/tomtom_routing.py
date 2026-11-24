
"""TomTom-measurement-aware routing with diagnostic timing.

Pipeline:
1. Calculate baseline routes.
2. Probe TomTom flow along the candidate corridor.
3. Match measurements to directed OSM edges.
4. Recalculate routes using measured traffic costs.

Only matched directed edges receive TomTom measurements.
All other edges retain modelled/simulated costs.
"""

from __future__ import annotations

import math
import time
from typing import Any

from routing.flow_matching import (
    EdgeIndex,
    FlowMeasurement,
    build_measured_edge_table,
    parse_flow_payload,
)

_INDEX_CACHE: dict[int, tuple[Any, EdgeIndex]] = {}


def debug(message: str) -> None:
    """Print diagnostic messages immediately in the backend terminal."""
    elapsed = time.perf_counter()
    print(f"[TOMTOM DEBUG] {message}", flush=True)


def edge_index_for(graph) -> EdgeIndex:
    hit = _INDEX_CACHE.get(id(graph))

    if hit is None or hit[0] is not graph:
        start = time.perf_counter()
        print("[TOMTOM DEBUG] Building OSM directed-edge index...", flush=True)

        hit = (graph, EdgeIndex.build(graph))

        _INDEX_CACHE.clear()
        _INDEX_CACHE[id(graph)] = hit

        print(
            f"[TOMTOM DEBUG] Edge index built in "
            f"{time.perf_counter() - start:.2f}s "
            f"({len(hit[1].edges)} directed edges)",
            flush=True,
        )

    return hit[1]


def sample_polyline(
    points: list[list[float]],
    spacing_m: float = 1200.0,
) -> list[tuple[float, float]]:
    """Sample points (lat, lon) along a polyline, including start and end."""

    if not points:
        return []

    out = [tuple(points[0])]
    carried = 0.0

    for (la1, lo1), (la2, lo2) in zip(points[:-1], points[1:]):
        dx = (
            (lo2 - lo1)
            * 111_320.0
            * math.cos(math.radians((la1 + la2) / 2))
        )
        dy = (la2 - la1) * 110_540.0

        seg = math.hypot(dx, dy)

        if seg == 0:
            continue

        d = spacing_m - carried

        while d <= seg:
            t = d / seg
            out.append((
                la1 + t * (la2 - la1),
                lo1 + t * (lo2 - lo1),
            ))
            d += spacing_m

        carried = (carried + seg) % spacing_m

    if out[-1] != tuple(points[-1]):
        out.append(tuple(points[-1]))

    return out


def probe_corridor(
    tomtom,
    routes: list[dict],
    *,
    max_calls: int = 6,
    spacing_m: float = 1200.0,
) -> tuple[list[FlowMeasurement], dict[str, Any]]:
    """Fetch TomTom flow measurements along candidate routes."""

    start = time.perf_counter()

    seen: set[tuple[float, float]] = set()
    points: list[tuple[float, float]] = []

    for route in routes:
        for point in sample_polyline(
            route.get("polyline_latlon") or [],
            spacing_m,
        ):
            key = (round(point[0], 4), round(point[1], 4))

            if key not in seen:
                seen.add(key)
                points.append(point)

    skipped = max(0, len(points) - max_calls)
    points = points[:max_calls]

    print(
        f"[TOMTOM DEBUG] Unique corridor points: {len(seen)} | "
        f"Selected probes: {len(points)} | "
        f"Skipped: {skipped}",
        flush=True,
    )

    measurements: list[FlowMeasurement] = []
    failed = 0
    errors: list[str] = []
    probe_times: list[float] = []

    for i, (lat, lon) in enumerate(points, start=1):
        probe_start = time.perf_counter()

        print(
            f"[TOMTOM DEBUG] Probe {i}/{len(points)} starting "
            f"at ({lat:.5f}, {lon:.5f})",
            flush=True,
        )

        try:
            payload = tomtom.flow_at(lat, lon)
            m = parse_flow_payload(payload, lat, lon)

            duration = time.perf_counter() - probe_start
            probe_times.append(duration)

            if m is None:
                failed += 1

                if payload:
                    error = payload.get("error") or payload.get("message")
                    if error:
                        errors.append(str(error)[:120])

                print(
                    f"[TOMTOM DEBUG] Probe {i} returned no usable "
                    f"measurement after {duration:.2f}s",
                    flush=True,
                )
            else:
                measurements.append(m)

                print(
                    f"[TOMTOM DEBUG] Probe {i} succeeded in "
                    f"{duration:.2f}s | "
                    f"speed={m.current_speed_kph} km/h | "
                    f"confidence={m.confidence}",
                    flush=True,
                )

        except Exception as exc:
            duration = time.perf_counter() - probe_start
            probe_times.append(duration)
            failed += 1
            errors.append(f"{type(exc).__name__}: {exc}"[:120])

            print(
                f"[TOMTOM DEBUG] Probe {i} raised "
                f"{type(exc).__name__} after {duration:.2f}s: {exc}",
                flush=True,
            )

    total = time.perf_counter() - start

    stats = {
        "probes_requested": len(points),
        "probes_ok": len(measurements),
        "probes_failed": failed,
        "probes_skipped_over_budget": skipped,
        "probe_times_seconds": [round(t, 2) for t in probe_times],
        "probe_total_seconds": round(total, 2),
        "errors": sorted(set(errors))[:3],
    }

    print(
        f"[TOMTOM DEBUG] All probes completed in {total:.2f}s | "
        f"successful={len(measurements)} | failed={failed}",
        flush=True,
    )

    return measurements, stats


def route_with_tomtom_flow(
    optimizer,
    tomtom,
    origin_node,
    destination_node,
    *,
    algorithm: str = "dijkstra",
    alternatives: int = 3,
    congestion_by_edge: dict | None = None,
    rainfall_mm: float = 0.0,
    max_calls: int = 6,
    spacing_m: float = 1200.0,
    min_confidence: float = 0.5,
) -> dict[str, Any]:
    """Baseline route -> TomTom probes -> edge matching -> rerouting."""

    total_start = time.perf_counter()

    kw = dict(
        algorithm=algorithm,
        alternatives=alternatives,
        congestion_by_edge=congestion_by_edge,
        rainfall_mm=rainfall_mm,
    )

    # ---------------------------------------------------------
    # 1. Baseline route
    # ---------------------------------------------------------

    stage_start = time.perf_counter()
    print("[TOMTOM DEBUG] Starting baseline route calculation...", flush=True)

    baseline = optimizer.route_nodes(
        origin_node,
        destination_node,
        **kw,
    )

    print(
        f"[TOMTOM DEBUG] Baseline route completed in "
        f"{time.perf_counter() - stage_start:.2f}s",
        flush=True,
    )

    info: dict[str, Any] = {
        "source": "tomtom_traffic_api",
        "status": "not_applied",
        "is_live": False,
        "edges_matched": 0,
        "measurements_matched": 0,
        "coverage_best_route": 0.0,
        "probes_requested": 0,
        "probes_ok": 0,
    }

    if not getattr(tomtom, "api_key", None):
        info.update(
            status="disabled",
            note=(
                "TOMTOM_API_KEY is not configured; routes use "
                "modelled/simulated costs only."
            ),
        )
        baseline["traffic_flow"] = info
        return baseline

    # ---------------------------------------------------------
    # 2. TomTom corridor probing
    # ---------------------------------------------------------

    print("[TOMTOM DEBUG] Starting TomTom flow probing...", flush=True)

    measurements, stats = probe_corridor(
        tomtom,
        baseline["routes"],
        max_calls=max_calls,
        spacing_m=spacing_m,
    )

    info.update(stats)

    if not measurements:
        info.update(
            status="unavailable",
            note=(
                "TomTom returned no usable flow data; routes use "
                "modelled/simulated costs only."
            ),
        )
        baseline["traffic_flow"] = info

        print(
            f"[TOMTOM DEBUG] No usable measurements. "
            f"Total elapsed: {time.perf_counter() - total_start:.2f}s",
            flush=True,
        )

        return baseline

    # ---------------------------------------------------------
    # 3. Build spatial index and match directed OSM edges
    # ---------------------------------------------------------

    print("[TOMTOM DEBUG] Starting spatial edge matching...", flush=True)

    match_start = time.perf_counter()

    index = edge_index_for(optimizer.graph)

    index_ready = time.perf_counter()

    match_stats: dict[str, int] = {}

    table = build_measured_edge_table(
        index,
        measurements,
        min_confidence=min_confidence,
        stats=match_stats,
    )

    match_duration = time.perf_counter() - index_ready

    print(
        f"[TOMTOM DEBUG] Spatial matching completed in "
        f"{match_duration:.2f}s | "
        f"matched edges={len(table)} | "
        f"measurements={len(measurements)}",
        flush=True,
    )

    print(
        f"[TOMTOM DEBUG] Index + matching stage: "
        f"{time.perf_counter() - match_start:.2f}s",
        flush=True,
    )

    info.update(
        edges_matched=len(table),
        **match_stats,
    )

    if not table:
        info.update(
            status="no_edge_match",
            note=(
                "Measurements were received but none matched a directed "
                "OSM edge (low confidence or geometry/direction mismatch); "
                "routes use modelled costs only."
            ),
        )

        baseline["traffic_flow"] = info

        print(
            f"[TOMTOM DEBUG] No directed OSM edges matched. "
            f"Total elapsed: {time.perf_counter() - total_start:.2f}s",
            flush=True,
        )

        return baseline

    # ---------------------------------------------------------
    # 4. Recalculate routes using measured traffic costs
    # ---------------------------------------------------------

    print("[TOMTOM DEBUG] Starting TomTom-aware route recalculation...", flush=True)

    reroute_start = time.perf_counter()

    result = optimizer.route_nodes(
        origin_node,
        destination_node,
        measured_by_edge=table,
        **kw,
    )

    reroute_duration = time.perf_counter() - reroute_start

    print(
        f"[TOMTOM DEBUG] TomTom-aware route recalculation "
        f"completed in {reroute_duration:.2f}s",
        flush=True,
    )

    best = result["routes"][0]
    share = best["measured_distance_share"]

    scope = (
        f"{share:.0%} of the best route's length is priced from measurements"
        if share > 0
        else (
            "the selected route contains no measured edges "
            "(the measurements priced the corridor roads and steered "
            "the choice away from them)"
        )
    )

    info.update(
        status="applied",
        is_live=True,
        coverage_best_route=share,
        note=(
            "Measured TomTom flow speeds replaced the modelled congestion "
            "factor on matched directed edges only; "
            f"{scope}. All other edges use modelled costs."
        ),
        closures_in_graph=sum(
            1 for record in table.values()
            if record["road_closure"]
        ),
        baseline_best_time_min=(
            baseline["routes"][0]["estimated_travel_time_min"]
        ),
        baseline_best_nodes=baseline["routes"][0]["node_sequence"],
    )

    result["traffic_flow"] = info

    print(
        f"[TOMTOM DEBUG] COMPLETE | "
        f"total={time.perf_counter() - total_start:.2f}s | "
        f"status=applied | matched_edges={len(table)}",
        flush=True,
    )

    return result
