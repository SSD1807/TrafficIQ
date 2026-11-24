
"""Spatial matching of TomTom flow measurements to DIRECTED OSM edges.

Uses a geographic grid index to reduce candidate searches while preserving
distance, direction, overlap and confidence checks.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np

MAX_FACTOR = 6.0

# Approximately 100 metres per cell around Bhubaneswar.
GRID_CELL_DEG = 0.001


@dataclass
class FlowMeasurement:
    query_lat: float
    query_lon: float
    current_speed_kph: float
    free_flow_speed_kph: float
    confidence: float
    road_closure: bool
    coordinates: list[tuple[float, float]]
    frc: str | None = None
    is_live: bool = True
    fetched_at: float | None = None

    @property
    def factor(self) -> float:
        if self.current_speed_kph <= 0:
            return MAX_FACTOR

        return float(
            min(
                MAX_FACTOR,
                max(1.0, self.free_flow_speed_kph / self.current_speed_kph),
            )
        )


def parse_flow_payload(
    payload: dict[str, Any] | None,
    lat: float,
    lon: float,
) -> FlowMeasurement | None:
    """Convert a TomTom response into a usable measurement."""

    if not payload or payload.get("status") != "ok" or not payload.get("is_live"):
        return None

    seg = (payload.get("data") or {}).get("flowSegmentData")

    if not seg:
        return None

    try:
        coords = [
            (float(c["latitude"]), float(c["longitude"]))
            for c in seg["coordinates"]["coordinate"]
        ]

        if len(coords) < 2:
            return None

        return FlowMeasurement(
            query_lat=lat,
            query_lon=lon,
            current_speed_kph=float(seg["currentSpeed"]),
            free_flow_speed_kph=float(seg["freeFlowSpeed"]),
            confidence=float(seg.get("confidence", 0.0)),
            road_closure=bool(seg.get("roadClosure", False)),
            coordinates=coords,
            frc=seg.get("frc"),
            is_live=True,
            fetched_at=payload.get("fetched_at"),
        )

    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------

def _xy(
    lat: float,
    lon: float,
    lat0: float,
    lon0: float,
) -> tuple[float, float]:
    return (
        (lon - lon0) * 111_320.0 * math.cos(math.radians(lat0)),
        (lat - lat0) * 110_540.0,
    )


def _bearing(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _point_segment(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> tuple[float, float]:
    """Distance and bearing from a point to a line segment."""

    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy

    t = (
        0.0
        if length_squared == 0
        else max(
            0.0,
            min(
                1.0,
                ((px - ax) * dx + (py - ay) * dy) / length_squared,
            ),
        )
    )

    nearest_x = ax + t * dx
    nearest_y = ay + t * dy

    return (
        math.hypot(px - nearest_x, py - nearest_y),
        _bearing(dx, dy),
    )


def _edge_samples(
    xy: list[tuple[float, float]],
    step_m: float = 10.0,
):
    """Yield sampled positions and bearings along an edge."""

    for (ax, ay), (bx, by) in zip(xy[:-1], xy[1:]):
        length = math.hypot(bx - ax, by - ay)

        if length == 0:
            continue

        n = max(1, int(length // step_m))
        bearing = _bearing(bx - ax, by - ay)

        for i in range(n):
            t = (i + 0.5) / n

            yield (
                ax + t * (bx - ax),
                ay + t * (by - ay),
                bearing,
            )


# ---------------------------------------------------------
# Spatial grid index
# ---------------------------------------------------------

@dataclass
class EdgeIndex:
    """Directed OSM edges with a geographic grid for fast lookup."""

    edges: list[tuple[Any, Any, Any]] = field(default_factory=list)

    # Each edge geometry is stored as (longitude, latitude).
    coords: list[list[tuple[float, float]]] = field(default_factory=list)

    bbox: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 4))
    )

    grid: dict[tuple[int, int], list[int]] = field(
        default_factory=dict
    )

    cell_size: float = GRID_CELL_DEG

    @staticmethod
    def _cell(lon: float, lat: float, cell_size: float):
        return (
            math.floor(lon / cell_size),
            math.floor(lat / cell_size),
        )

    @classmethod
    def build(cls, graph: nx.Graph) -> "EdgeIndex":
        """Build the directed-edge index and geographic grid."""

        started = time.perf_counter()

        idx = cls()
        boxes = []

        multi = graph.is_multigraph()

        if multi:
            edge_iterator = graph.edges(keys=True, data=True)
        else:
            edge_iterator = (
                (u, v, None, data)
                for u, v, data in graph.edges(data=True)
            )

        for u, v, key, data in edge_iterator:

            geom = data.get("geometry")

            if geom is not None and hasattr(geom, "coords"):
                pts = [
                    (float(x), float(y))
                    for x, y in geom.coords
                ]
            else:
                a = graph.nodes[u]
                b = graph.nodes[v]

                pts = [
                    (float(a["x"]), float(a["y"])),
                    (float(b["x"]), float(b["y"])),
                ]

            if len(pts) < 2:
                continue

            edge_id = len(idx.edges)

            idx.edges.append((u, v, key))
            idx.coords.append(pts)

            xs, ys = zip(*pts)

            min_lon, max_lon = min(xs), max(xs)
            min_lat, max_lat = min(ys), max(ys)

            boxes.append(
                (min_lon, min_lat, max_lon, max_lat)
            )

            # Register this directed edge in every grid cell
            # intersected by its bounding box.
            min_cx, min_cy = cls._cell(
                min_lon, min_lat, idx.cell_size
            )

            max_cx, max_cy = cls._cell(
                max_lon, max_lat, idx.cell_size
            )

            for cx in range(min_cx, max_cx + 1):
                for cy in range(min_cy, max_cy + 1):
                    idx.grid.setdefault(
                        (cx, cy), []
                    ).append(edge_id)

        idx.bbox = (
            np.asarray(boxes, dtype=float)
            if boxes
            else np.zeros((0, 4))
        )

        print(
            f"[MATCH DEBUG] Spatial grid built in "
            f"{time.perf_counter() - started:.2f}s | "
            f"edges={len(idx.edges)} | "
            f"occupied cells={len(idx.grid)}",
            flush=True,
        )

        return idx

    def candidates(
        self,
        m: FlowMeasurement,
        margin_m: float,
    ) -> list[int]:
        """Find nearby directed edges using individual measurement segments."""

        if not self.edges or len(m.coordinates) < 2:
            return []

        lats = [p[0] for p in m.coordinates]
        mean_lat = sum(lats) / len(lats)

        dlat = margin_m / 110_540.0

        cosine = max(
            0.01,
            abs(math.cos(math.radians(mean_lat))),
        )

        dlon = margin_m / (111_320.0 * cosine)

        found: set[int] = set()

        # Query each TomTom segment separately instead of using
        # one large bounding box for the entire polyline.
        for (lat1, lon1), (lat2, lon2) in zip(
            m.coordinates[:-1],
            m.coordinates[1:],
        ):

            min_lon = min(lon1, lon2) - dlon
            max_lon = max(lon1, lon2) + dlon

            min_lat = min(lat1, lat2) - dlat
            max_lat = max(lat1, lat2) + dlat

            min_cx, min_cy = self._cell(
                min_lon, min_lat, self.cell_size
            )

            max_cx, max_cy = self._cell(
                max_lon, max_lat, self.cell_size
            )

            for cx in range(min_cx, max_cx + 1):
                for cy in range(min_cy, max_cy + 1):

                    found.update(
                        self.grid.get((cx, cy), ())
                    )

        return sorted(found)


# ---------------------------------------------------------
# Directed geometry matching
# ---------------------------------------------------------

def match_measurement(
    index: EdgeIndex,
    m: FlowMeasurement,
    *,
    max_dist_m: float = 35.0,
    max_bearing_deg: float = 40.0,
    min_overlap: float = 0.6,
) -> list[tuple[Any, Any, Any, float]]:
    """Return directed edges that match the measured segment."""

    if len(m.coordinates) < 2:
        return []

    lat0 = sum(c[0] for c in m.coordinates) / len(m.coordinates)
    lon0 = sum(c[1] for c in m.coordinates) / len(m.coordinates)

    tt = [
        _xy(lat, lon, lat0, lon0)
        for lat, lon in m.coordinates
    ]

    # Precompute TomTom segments in local projected coordinates.
    measured_segments = list(zip(tt[:-1], tt[1:]))

    out = []

    candidate_ids = index.candidates(
        m,
        max_dist_m + 10.0,
    )

    print(
        f"[MATCH DEBUG] Geometry candidates: "
        f"{len(candidate_ids)}",
        flush=True,
    )

    for i in candidate_ids:

        edge_xy = [
            _xy(lat, lon, lat0, lon0)
            for lon, lat in index.coords[i]
        ]

        hits = 0
        total = 0

        for sx, sy, edge_bearing in _edge_samples(edge_xy):

            total += 1
            best_distance = float("inf")
            best_bearing = 0.0

            for (ax, ay), (bx, by) in measured_segments:

                distance, bearing = _point_segment(
                    sx, sy, ax, ay, bx, by
                )

                if distance < best_distance:
                    best_distance = distance
                    best_bearing = bearing

            if (
                best_distance <= max_dist_m
                and _angle_diff(
                    edge_bearing,
                    best_bearing,
                ) <= max_bearing_deg
            ):
                hits += 1

        if total and hits / total >= min_overlap:
            u, v, key = index.edges[i]

            out.append(
                (u, v, key, hits / total)
            )

    return out


# ---------------------------------------------------------
# Measured edge table
# ---------------------------------------------------------

def build_measured_edge_table(
    index: EdgeIndex,
    measurements: list[FlowMeasurement],
    *,
    min_confidence: float = 0.5,
    stats: dict | None = None,
    **match_kwargs,
) -> dict[tuple, dict[str, Any]]:
    """Build the measured directed-edge cost table."""

    started = time.perf_counter()

    table: dict[tuple, dict[str, Any]] = {}

    n_low = 0
    n_matched = 0

    print(
        f"[MATCH DEBUG] Starting spatial matching: "
        f"{len(measurements)} measurements",
        flush=True,
    )

    for number, m in enumerate(measurements, start=1):

        if m.confidence < min_confidence:
            n_low += 1
            continue

        measurement_start = time.perf_counter()

        candidates = index.candidates(
            m,
            match_kwargs.get("max_dist_m", 35.0) + 10.0,
        )

        print(
            f"[MATCH DEBUG] Measurement {number}: "
            f"{len(candidates)} candidates",
            flush=True,
        )

        matches = match_measurement(
            index,
            m,
            **match_kwargs,
        )

        n_matched += bool(matches)

        for u, v, key, overlap in matches:

            prev = table.get((u, v))
            score = (m.confidence, overlap)

            if prev is None or score > prev["_score"]:

                table[(u, v)] = {
                    "factor": m.factor,
                    "current_kph": m.current_speed_kph,
                    "free_flow_kph": m.free_flow_speed_kph,
                    "confidence": m.confidence,
                    "road_closure": m.road_closure,
                    "frc": m.frc,
                    "overlap": round(overlap, 2),
                    "_score": score,
                }

        print(
            f"[MATCH DEBUG] Measurement {number}: "
            f"{len(matches)} matched edges in "
            f"{time.perf_counter() - measurement_start:.2f}s",
            flush=True,
        )

    for record in table.values():
        record.pop("_score", None)

    if stats is not None:
        stats.update(
            measurements_low_confidence=n_low,
            measurements_matched=n_matched,
        )

    print(
        f"[MATCH DEBUG] Matching completed in "
        f"{time.perf_counter() - started:.2f}s | "
        f"matched edges={len(table)}",
        flush=True,
    )

    return table
