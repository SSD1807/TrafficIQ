"""Match a provider incident's geometry to DIRECTED edges of the OSM road graph.

Method (a small, explainable map-matcher):

1. The incident geometry (GeoJSON Point / LineString, lon/lat) is sampled every ~10 m (at most ``MAX_SAMPLES`` samples).
2. For each sample, nearby directed edges are fetched from the existing grid ``EdgeIndex`` (routing/flow_matching.py).
   An edge is an *option* when the sample lies within ``max_distance_m`` of the edge polyline AND (for LineStrings,
   whose vertex order gives a travel direction) the edge's local bearing agrees with the incident's local bearing within
   ``max_bearing_deg``. This is what stops an incident on one carriageway from being applied to the opposite one.
3. Options are grouped per *road* (OSM way id). If two different roads are both plausible for a sample and their
   distances differ by less than ``ambiguity_margin_m`` (and a road number cannot decide), the sample is *ambiguous*:
   we do not guess between parallel roads.
4. An incident is **matched** only when enough of it is covered by unambiguous samples and the confidence is high
   enough. Otherwise it is **unmatched** with an explicit reason, and nothing is applied to any road edge.

Direction assumption: TomTom's documentation does not state that LineString vertex order follows the direction of
travel. It is assumed to (this is how traffic-incident geometries are normally delivered); set
``direction_aware=False`` to disable the bearing test if you find otherwise. Point incidents carry no direction, so the
nearest road is matched and, when the reverse edge exists, both directions are flagged.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from routing.flow_matching import EdgeIndex, _angle_diff, _bearing, _point_segment, _xy

MAX_SAMPLES = 120
SAMPLE_STEP_M = 10.0

R_NO_ROAD = "no_nearby_road"
R_DIRECTION = "direction_mismatch"
R_AMBIGUOUS = "ambiguous_parallel_roads"
R_LOW_CONF = "low_confidence"
R_BAD_GEOMETRY = "invalid_geometry"
R_NO_GRAPH = "road_graph_not_openstreetmap"
R_OUTSIDE = "outside_road_graph"

REASON_TEXT = {
    R_NO_ROAD: "No road segment of the OpenStreetMap graph lies close enough to the reported location.",
    R_DIRECTION: "Nearby roads run in a different direction from the reported incident.",
    R_AMBIGUOUS: "Several roads (e.g. parallel carriageways or service roads) fit equally well; not applied to avoid the wrong road.",
    R_LOW_CONF: "The geometry only partly overlaps a road segment, so the match is not confident enough.",
    R_BAD_GEOMETRY: "The provider geometry could not be used.",
    R_NO_GRAPH: "The active road graph is not OpenStreetMap geometry (synthetic fallback), so provider coordinates cannot be matched.",
    R_OUTSIDE: "The incident lies outside the loaded road graph.",
}


@dataclass
class MatchConfig:
    max_distance_m: float = 30.0
    max_bearing_deg: float = 45.0
    ambiguity_margin_m: float = 8.0
    min_coverage: float = 0.6          # share of samples that must land on an unambiguous edge
    max_ambiguous_share: float = 0.2
    min_confidence: float = 0.55
    direction_aware: bool = True


@dataclass
class MatchResult:
    status: str                                     # "matched" | "unmatched"
    confidence: float = 0.0
    edges: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    reason_text: str | None = None
    method: str = "geometry_sampling"
    direction_used: bool = False
    bidirectional: bool = False                     # Point incident: apply to the reverse edge too (if it exists)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return self.status == "matched"

    def to_public(self) -> dict[str, Any]:
        return {"status": self.status, "confidence": round(self.confidence, 3), "edges": self.edges, "reason": self.reason,
                "reason_text": self.reason_text, "method": self.method, "direction_used": self.direction_used,
                "bidirectional": self.bidirectional, "detail": self.detail}


def _unmatched(reason: str, **detail: Any) -> MatchResult:
    return MatchResult("unmatched", 0.0, [], reason, REASON_TEXT[reason], detail=detail)


def _norm_ref(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _edge_attrs(graph, u, v, key) -> dict[str, Any]:
    try:
        return graph[u][v][key] if key is not None else graph[u][v]
    except KeyError:
        return {}


def _road_key(attrs: dict[str, Any], edge: tuple) -> Any:
    osmid = attrs.get("osmid")
    if isinstance(osmid, (list, tuple)):
        osmid = tuple(sorted(str(o) for o in osmid))
    return osmid if osmid is not None else (attrs.get("name") or attrs.get("ref") or edge[:2])


def _refs(attrs: dict[str, Any]) -> set[str]:
    ref = attrs.get("ref")
    parts = ref if isinstance(ref, (list, tuple)) else [ref] if ref else []
    out: set[str] = set()
    for p in parts:
        out.update(_norm_ref(x) for x in str(p).split(";") if x.strip())
    return out


def _sample_polyline(xy: list[tuple[float, float]], max_samples: int = MAX_SAMPLES) -> list[tuple[float, float, float]]:
    """Samples (x, y, bearing) along a projected polyline, evenly spaced (>= SAMPLE_STEP_M apart), always incl. the ends."""
    seg = [(math.hypot(b[0] - a[0], b[1] - a[1]), a, b) for a, b in zip(xy[:-1], xy[1:])]
    total = sum(s[0] for s in seg)
    if total <= 0:
        return []
    n = max(2, min(max_samples, int(total // SAMPLE_STEP_M) + 1))
    out: list[tuple[float, float, float]] = []
    for i in range(n):
        target, run = total * i / (n - 1), 0.0
        for length, a, b in seg:
            if length > 0 and run + length >= target - 1e-9:
                t = min(1.0, max(0.0, (target - run) / length))
                out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), _bearing(b[0] - a[0], b[1] - a[1])))
                break
            run += length
    return out


def _candidates(index: EdgeIndex, lat: float, lon: float, margin_m: float) -> set[int]:
    dlat = margin_m / 110_540.0
    dlon = margin_m / (111_320.0 * max(0.01, math.cos(math.radians(lat))))
    cx0, cy0 = index._cell(lon - dlon, lat - dlat, index.cell_size)
    cx1, cy1 = index._cell(lon + dlon, lat + dlat, index.cell_size)
    found: set[int] = set()
    for cx in range(cx0, cx1 + 1):
        for cy in range(cy0, cy1 + 1):
            found.update(index.grid.get((cx, cy), ()))
    return found


def _name_hit(attrs: dict[str, Any], hint_text: str) -> bool:
    """True when the OSM name of this edge appears in the provider's own from/to/description text (>= 4 letters)."""
    raw = attrs.get("name")
    for name in (raw if isinstance(raw, (list, tuple)) else [raw]):
        n = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower()).strip()
        if len(n) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", hint_text):
            return True
    return False


def match_incident(graph, index: EdgeIndex, geometry: dict[str, Any] | None, *, road_numbers: list[str] | None = None,
                   name_hints: list[str] | None = None, config: MatchConfig | None = None) -> MatchResult:
    """Match one provider incident geometry to directed edges. Never raises for bad input; returns 'unmatched'."""
    cfg = config or MatchConfig()
    if not index.edges:
        return _unmatched(R_NO_ROAD, note="empty road index")
    try:
        gtype = geometry["type"]  # type: ignore[index]
        coords = geometry["coordinates"]  # type: ignore[index]
        pts = [coords] if gtype == "Point" else list(coords)
        latlon = [(float(c[1]), float(c[0])) for c in pts]
        if gtype not in ("Point", "LineString") or not latlon or (gtype == "LineString" and len(latlon) < 2):
            return _unmatched(R_BAD_GEOMETRY)
    except (TypeError, KeyError, ValueError, IndexError):
        return _unmatched(R_BAD_GEOMETRY)

    lat0 = sum(p[0] for p in latlon) / len(latlon)
    lon0 = sum(p[1] for p in latlon) / len(latlon)
    is_point = gtype == "Point" or all(p == latlon[0] for p in latlon)
    directional = cfg.direction_aware and not is_point

    if is_point:
        samples = [(0.0, 0.0, None)]
    else:
        samples = _sample_polyline([_xy(la, lo, lat0, lon0) for la, lo in latlon])
        if not samples:
            return _unmatched(R_BAD_GEOMETRY)

    # Quick containment check: is the incident anywhere near the graph at all?
    lo_min, la_min, lo_max, la_max = (float(index.bbox[:, 0].min()), float(index.bbox[:, 1].min()),
                                       float(index.bbox[:, 2].max()), float(index.bbox[:, 3].max()))
    if not (lo_min - 0.002 <= lon0 <= lo_max + 0.002 and la_min - 0.002 <= lat0 <= la_max + 0.002):
        return _unmatched(R_OUTSIDE)

    wanted_refs = {_norm_ref(r) for r in (road_numbers or []) if r}
    hint_text = re.sub(r"[^a-z0-9 ]+", " ", " ; ".join(h for h in (name_hints or []) if h).lower())
    ambiguous_roads: list[str] = []
    xy_cache: dict[int, list[tuple[float, float]]] = {}

    def edge_xy(i: int) -> list[tuple[float, float]]:
        if i not in xy_cache:
            xy_cache[i] = [_xy(la, lo, lat0, lon0) for lo, la in index.coords[i]]
        return xy_cache[i]

    assigned: dict[int, list[float]] = {}
    ambiguous = unmatched_samples = 0
    saw_wrong_direction = False
    for sx, sy, sb in samples:
        # Convert the sample back to lat/lon for the grid lookup.
        slat = lat0 + sy / 110_540.0
        slon = lon0 + sx / (111_320.0 * math.cos(math.radians(lat0)))
        per_road: dict[Any, tuple[float, int, bool]] = {}   # road -> (best distance, edge idx, ref hint)
        any_near = False
        for i in _candidates(index, slat, slon, cfg.max_distance_m + 10.0):
            best_d, best_b = float("inf"), 0.0
            exy = edge_xy(i)
            for (ax, ay), (bx, by) in zip(exy[:-1], exy[1:]):
                d, b = _point_segment(sx, sy, ax, ay, bx, by)
                if d < best_d:
                    best_d, best_b = d, b
            if best_d > cfg.max_distance_m:
                continue
            any_near = True
            if directional and sb is not None and _angle_diff(best_b, sb) > cfg.max_bearing_deg:
                saw_wrong_direction = True
                continue
            u, v, key = index.edges[i]
            attrs = _edge_attrs(graph, u, v, key)
            hint = bool(wanted_refs and wanted_refs & _refs(attrs)) or bool(hint_text and _name_hit(attrs, hint_text))
            rk = _road_key(attrs, index.edges[i])
            if rk not in per_road or best_d < per_road[rk][0]:
                per_road[rk] = (best_d, i, hint)
        if not per_road:
            unmatched_samples += 1
            continue
        ranked = sorted(per_road.values(), key=lambda t: (not t[2], t[0]))
        if len(ranked) > 1 and not (ranked[0][2] and not ranked[1][2]):
            if ranked[1][0] - ranked[0][0] < cfg.ambiguity_margin_m:
                ambiguous += 1
                for _d, _i, _h in ranked[:2]:
                    nm = _edge_attrs(graph, *index.edges[_i]).get("name")
                    nm = nm[0] if isinstance(nm, (list, tuple)) and nm else nm
                    label = nm if isinstance(nm, str) and nm else "unnamed road"
                    if label not in ambiguous_roads and len(ambiguous_roads) < 4:
                        ambiguous_roads.append(label)
                continue
        d, i, _ = ranked[0]
        assigned.setdefault(i, []).append(d)

    n = len(samples)
    got = sum(len(v) for v in assigned.values())
    detail = {"samples": n, "assigned_samples": got, "ambiguous_samples": ambiguous, "no_road_samples": unmatched_samples}
    if ambiguous_roads:
        detail["ambiguous_between"] = ambiguous_roads
    if got == 0:
        if ambiguous:
            return _unmatched(R_AMBIGUOUS, **detail)
        return _unmatched(R_DIRECTION if saw_wrong_direction else R_NO_ROAD, **detail)
    coverage, amb_share = got / n, ambiguous / n
    if amb_share > cfg.max_ambiguous_share and coverage < 1 - cfg.max_ambiguous_share:
        return _unmatched(R_AMBIGUOUS, coverage=round(coverage, 3), **detail)
    if coverage < cfg.min_coverage:
        return _unmatched(R_LOW_CONF, coverage=round(coverage, 3), **detail)

    keep = {i: ds for i, ds in assigned.items() if len(ds) >= 2 or len(ds) / n >= 0.1 or n <= 2}
    if not keep:
        return _unmatched(R_LOW_CONF, coverage=round(coverage, 3), **detail)
    kept_samples = sum(len(v) for v in keep.values())
    mean_d = sum(sum(v) for v in keep.values()) / kept_samples
    confidence = (kept_samples / n) * (1.0 - 0.5 * min(1.0, mean_d / cfg.max_distance_m)) * (1.0 - amb_share)
    if confidence < cfg.min_confidence:
        return _unmatched(R_LOW_CONF, confidence=round(confidence, 3), **detail)

    edges, bidirectional = [], False
    for i, ds in sorted(keep.items(), key=lambda kv: kv[0]):
        u, v, key = index.edges[i]
        attrs = _edge_attrs(graph, u, v, key)
        edges.append({"u": str(u), "v": str(v), "key": None if key is None else str(key),
                      "length_m": round(float(attrs.get("length", 0.0) or 0.0), 1),
                      "road_name": attrs.get("name") if isinstance(attrs.get("name"), str) else None,
                      "highway": str(attrs.get("highway") if not isinstance(attrs.get("highway"), list) else attrs["highway"][0]),
                      "samples": len(ds), "mean_distance_m": round(sum(ds) / len(ds), 1)})
    if is_point:
        # No direction information: flag the reverse edge too when the graph has one.
        bidirectional = any(graph.has_edge(index.edges[i][1], index.edges[i][0]) for i in keep)
        # A point resolves to ONE road segment; keep only the closest edge.
        best = min(keep, key=lambda i: sum(keep[i]) / len(keep[i]))
        edges = [e for e in edges if (e["u"], e["v"], e["key"]) == (str(index.edges[best][0]), str(index.edges[best][1]),
                                                                      None if index.edges[best][2] is None else str(index.edges[best][2]))]
    return MatchResult("matched", float(confidence), edges, None, None, direction_used=directional, bidirectional=bidirectional,
                       detail={**detail, "coverage": round(coverage, 3), "mean_distance_m": round(mean_d, 1)})
