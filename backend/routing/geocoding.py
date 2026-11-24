"""Place search for Smart Route Planner.

Why this module exists
----------------------
The previous implementation called ``osmnx.geocode("<query>, Bhubaneswar, Odisha, India")``. That is ONE free-text
Nominatim request with no country filter, no bounding box, no spelling tolerance and no local fallback, and every
failure (no hit, HTTP error, timeout, blocked user agent) was reported with the same text
("Could not geocode query using Nominatim"). Small Odia locality names such as *Jharapada* or *Baragada* are often
indexed under a different spelling or only as an ``place=neighbourhood`` node, so the single query returned nothing.

Strategy (first hit wins, every step is reported in ``GeocodeResult.attempts``):

1. **Local road-name index** built from the loaded OSM graph (exact, then fuzzy). No network needed.
2. **Nominatim free-text** restricted to India (``countrycodes=in``) and *bounded* to the road-graph bounding box.
3. **Nominatim structured** query (``street`` / ``city`` / ``state``).
4. The same two steps for **transliteration variants** of the query (``jharapada`` -> ``jharpada`` ...).
5. Fuzzy comparison of returned display names against the query, so a wrong-but-nearby hit is not accepted blindly.

Failures are typed (``GeocodeError.code``): ``empty_query``, ``not_found``, ``outside_network``,
``geocoder_unavailable`` - so the API/UI can say *why* instead of one generic message. Nothing is invented: a result
always comes from the local OSM graph or from Nominatim, and is validated against the loaded graph's bounding box.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

LOG = logging.getLogger(__name__)

DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org"
DEFAULT_USER_AGENT = "TrafficIQ-Bhubaneswar/1.0 (final-year project; set NOMINATIM_USER_AGENT in backend/.env)"
CITY_SUFFIXES = ("bhubaneswar", "bhubaneshwar", "odisha", "orissa", "india")

# Documented transliteration rules for Indian place names (Latin spelling is not standardised). Applied one at a
# time, never combined, so the number of variants stays small and every variant is explainable.
TRANSLITERATION_RULES: tuple[tuple[str, str], ...] = (
    ("aa", "a"), ("ee", "i"), ("oo", "u"), ("w", "v"), ("sh", "s"), ("kh", "k"), ("jh", "j"), ("dh", "d"),
    ("ph", "f"), ("bh", "b"), ("gh", "g"), ("ra", "r"), ("ri", "ru"), ("ga", "gh"), ("da", "ra"), ("ra", "da"),
)
ROAD_WORDS = re.compile(r"\b(road|rd|street|st|marg|nagar|colony|chowk|square|junction)\b\.?", re.I)


class GeocodeError(ValueError):
    """A geocoding failure with a machine-readable ``code`` and a user-readable message."""

    def __init__(self, code: str, message: str, *, attempts: list[dict[str, Any]] | None = None,
                 suggestions: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.attempts = attempts or []
        self.suggestions = suggestions or []

    def to_public(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "attempts": self.attempts, "suggestions": self.suggestions}


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str
    source: str                      # "road_graph_name" | "nominatim"
    query: str
    query_used: str
    match_quality: float             # 0..1 similarity between the query and the matched name
    osm_type: str | None = None
    osm_id: int | None = None
    category: str | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {"latitude": self.latitude, "longitude": self.longitude, "display_name": self.display_name,
                "source": self.source, "query": self.query, "query_used": self.query_used,
                "match_quality": round(self.match_quality, 3), "osm_type": self.osm_type, "osm_id": self.osm_id,
                "category": self.category, "attempts": self.attempts}


def normalise(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())
    return re.sub(r"\s+", " ", text).strip()


def strip_city(query: str) -> str:
    parts = [p.strip() for p in re.split(r"[,;]", query) if p.strip()]
    kept = [p for p in parts if normalise(p) not in CITY_SUFFIXES]
    return ", ".join(kept) if kept else query.strip()


def spelling_variants(name: str) -> list[str]:
    """Distinct, ordered variants of ``name`` (original first)."""
    base = normalise(name)
    out = [base]
    for old, new in TRANSLITERATION_RULES:
        if old in base:
            v = base.replace(old, new, 1)
            if v not in out:
                out.append(v)
    bare = normalise(ROAD_WORDS.sub(" ", base))
    if bare and bare not in out:
        out.append(bare)
    return out


def similarity(a: str, b: str) -> float:
    a, b = normalise(a), normalise(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b.split() or a in b:
        return max(0.85, difflib.SequenceMatcher(None, a, b).ratio())
    return difflib.SequenceMatcher(None, a, b).ratio()


# The injected fetcher makes the module testable without internet: fetch(url, params, headers, timeout) -> parsed JSON.
Fetcher = Callable[[str, dict[str, Any], dict[str, str], float], Any]


def _default_fetch(url: str, params: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    import httpx  # declared in requirements.txt
    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


class Geocoder:
    def __init__(self, *, bbox: tuple[float, float, float, float], road_names: dict[str, tuple[float, float]] | None = None,
                 fetch: Fetcher | None = None, base_url: str | None = None, user_agent: str | None = None,
                 cache_path: str | Path | None = None, timeout: float = 10.0, min_interval_s: float = 1.1,
                 min_quality: float = 0.55, enabled: bool = True) -> None:
        """``bbox`` = (min_lon, min_lat, max_lon, max_lat) of the loaded road graph (+ margin is applied here).

        ``road_names`` maps a lower-cased OSM road name to a representative (lat, lon) taken from the graph itself.
        """
        self.bbox = bbox
        self.road_names = road_names or {}
        self._fetch = fetch or _default_fetch
        self.base_url = (base_url or os.getenv("NOMINATIM_BASE_URL") or DEFAULT_NOMINATIM_URL).rstrip("/")
        self.user_agent = user_agent or os.getenv("NOMINATIM_USER_AGENT") or DEFAULT_USER_AGENT
        self.timeout = timeout
        self.min_interval_s = min_interval_s     # Nominatim usage policy: max 1 request per second
        self.min_quality = min_quality
        self.enabled = enabled
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._last_call = 0.0
        if self.cache_path and self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                LOG.warning("Geocode cache unreadable, starting empty: %s", self.cache_path)

    # ------------------------------------------------------------------ helpers
    def _inside(self, lat: float, lon: float, margin: float = 0.02) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return min_lat - margin <= lat <= max_lat + margin and min_lon - margin <= lon <= max_lon + margin

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            LOG.warning("Could not write geocode cache: %s", exc)

    def _request(self, endpoint: str, params: dict[str, Any]) -> Any:
        key = endpoint + "?" + json.dumps(params, sort_keys=True)
        if key in self._cache:
            return self._cache[key]
        with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            data = self._fetch(f"{self.base_url}/{endpoint}", params, {"User-Agent": self.user_agent, "Accept-Language": "en"}, self.timeout)
            self._last_call = time.monotonic()
        self._cache[key] = data
        self._save_cache()
        return data

    def _nominatim_params(self, extra: dict[str, Any]) -> dict[str, Any]:
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return {"format": "jsonv2", "limit": 5, "addressdetails": 1, "countrycodes": "in",
                "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}", "bounded": 1, **extra}

    # ------------------------------------------------------------------ public
    def geocode(self, query: str) -> GeocodeResult:
        raw = str(query or "").strip()
        if not raw:
            raise GeocodeError("empty_query", "Enter a place name, road, landmark or address in Bhubaneswar.")
        core = strip_city(raw)
        attempts: list[dict[str, Any]] = []
        unavailable = 0

        local = self._local(core, attempts)
        if local:
            return local

        if self.enabled:
            for variant in spelling_variants(core):
                if unavailable >= 2:      # service is down: do not hammer it with every spelling variant
                    break
                for label, endpoint, extra in (
                    ("free_text", "search", {"q": f"{variant}, Bhubaneswar, Odisha, India"}),
                    ("structured", "search", {"street": variant, "city": "Bhubaneswar", "state": "Odisha", "country": "India"}),
                ):
                    step = {"step": f"nominatim_{label}", "query": variant}
                    try:
                        rows = self._request(endpoint, self._nominatim_params(extra))
                    except Exception as exc:  # network, HTTP 403/429/5xx, bad JSON
                        unavailable += 1
                        step.update(outcome="error", error=f"{type(exc).__name__}: {str(exc)[:160]}")
                        attempts.append(step)
                        continue
                    best = self._pick(core, variant, rows or [], attempts_step=step)
                    attempts.append(step)
                    if best:
                        best.attempts = attempts
                        best.query = raw
                        return best
            if attempts and unavailable and unavailable == sum(1 for a in attempts if a["step"].startswith("nominatim")):
                raise GeocodeError(
                    "geocoder_unavailable",
                    f"The geocoding service (Nominatim at {self.base_url}) could not be reached, so '{raw}' could not be "
                    "looked up. This is a connection/service problem, not proof that the place does not exist. Check the "
                    "internet connection, or pick a location from the road-name suggestions.",
                    attempts=attempts, suggestions=self.suggest(core))
        else:
            attempts.append({"step": "nominatim", "outcome": "disabled", "note": "NOMINATIM_ENABLED=false"})

        raise GeocodeError(
            "not_found",
            f"No location matching '{raw}' was found inside the Bhubaneswar road network. Try a nearby main road, a "
            "landmark, or a fuller address (e.g. 'Jharapada Road, Bhubaneswar').",
            attempts=attempts, suggestions=self.suggest(core))

    # ------------------------------------------------------------------ reverse geocoding (labels only)
    @staticmethod
    def _reverse_key(lat: float, lon: float) -> str:
        return "reverse?" + json.dumps({"lat": round(lat, 5), "lon": round(lon, 5), "zoom": 17}, sort_keys=True)

    @staticmethod
    def _reverse_summary(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict) or "address" not in row:
            return None
        a = row["address"] or {}
        road = a.get("road") or a.get("pedestrian") or a.get("footway") or a.get("highway")
        area = a.get("neighbourhood") or a.get("suburb") or a.get("residential") or a.get("quarter") or a.get("village") or a.get("hamlet")
        city = a.get("city") or a.get("town") or a.get("city_district")
        if not (road or area):
            return None
        return {"road": road, "area": area, "city": city, "display_name": row.get("display_name"), "source": "nominatim_reverse"}

    def peek_reverse(self, lat: float, lon: float) -> dict[str, Any] | None:
        """Cache-only lookup: never touches the network."""
        return self._reverse_summary(self._cache.get(self._reverse_key(lat, lon)))

    def reverse(self, lat: float, lon: float) -> dict[str, Any] | None:
        """Nominatim reverse geocode (cached, rate limited). Returns None when disabled, unreachable or nothing is known."""
        cached = self.peek_reverse(lat, lon)
        if cached or not self.enabled:
            return cached
        try:
            row = self._request("reverse", {"format": "jsonv2", "lat": round(lat, 5), "lon": round(lon, 5), "zoom": 17, "addressdetails": 1})
        except Exception as exc:
            LOG.info("Reverse geocode unavailable: %s", exc)
            return None
        return self._reverse_summary(row)

    def suggest(self, query: str, limit: int = 5) -> list[str]:
        names = list(self.road_names)
        close = difflib.get_close_matches(normalise(query), names, n=limit, cutoff=0.5)
        return [n.title() for n in close]

    # ------------------------------------------------------------------ steps
    def _local(self, core: str, attempts: list[dict[str, Any]]) -> GeocodeResult | None:
        q = normalise(core)
        step = {"step": "road_graph_names", "query": q}
        if q in self.road_names:
            lat, lon = self.road_names[q]
            step["outcome"] = "exact"
            attempts.append(step)
            return GeocodeResult(lat, lon, f"{q.title()} (OpenStreetMap road in the loaded network)", "road_graph_name", core, q, 1.0, attempts=attempts)
        best = max(((similarity(q, n), n) for n in self.road_names), default=(0.0, ""))
        if best[0] >= 0.88:
            lat, lon = self.road_names[best[1]]
            step.update(outcome="fuzzy", matched=best[1], quality=round(best[0], 3))
            attempts.append(step)
            return GeocodeResult(lat, lon, f"{best[1].title()} (OpenStreetMap road in the loaded network)", "road_graph_name", core, q, best[0], attempts=attempts)
        step["outcome"] = "no_match"
        attempts.append(step)
        return None

    def _pick(self, core: str, variant: str, rows: Iterable[dict[str, Any]], *, attempts_step: dict[str, Any]) -> GeocodeResult | None:
        scored: list[tuple[float, dict[str, Any]]] = []
        outside = 0
        for row in rows:
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not self._inside(lat, lon):
                outside += 1
                continue
            addr = row.get("address") or {}
            names = [row.get("name") or "", row.get("display_name") or "", *[str(v) for v in addr.values()]]
            q = max(similarity(core, n) for n in names if n) if any(names) else 0.0
            q = max(q, similarity(variant, row.get("name") or ""))
            scored.append((q, row))
        attempts_step["returned"] = len(list(rows)) if not isinstance(rows, list) else len(rows)
        attempts_step["outside_network"] = outside
        if not scored:
            attempts_step["outcome"] = "outside_network" if outside else "no_results"
            return None
        scored.sort(key=lambda t: -t[0])
        q, row = scored[0]
        if q < self.min_quality:
            attempts_step.update(outcome="low_similarity", best_quality=round(q, 3), best_name=row.get("display_name"))
            return None
        attempts_step.update(outcome="hit", quality=round(q, 3))
        return GeocodeResult(float(row["lat"]), float(row["lon"]), row.get("display_name") or core, "nominatim", core, variant, q,
                             osm_type=row.get("osm_type"), osm_id=row.get("osm_id"), category=row.get("category") or row.get("class"))


def road_name_index(graph) -> dict[str, tuple[float, float]]:
    """lower-cased road/node name -> (lat, lon), taken only from the loaded graph (edge names use a mid-point of the edge)."""
    names: dict[str, list[tuple[float, float]]] = {}
    for u, v, data in graph.edges(data=True):
        raw = data.get("name")
        for name in (raw if isinstance(raw, (list, tuple)) else [raw]):
            if not isinstance(name, str) or not name.strip():
                continue
            a, b = graph.nodes[u], graph.nodes[v]
            try:
                names.setdefault(normalise(name), []).append(((float(a["y"]) + float(b["y"])) / 2, (float(a["x"]) + float(b["x"])) / 2))
            except (KeyError, TypeError, ValueError):
                continue
    for _node, attrs in graph.nodes(data=True):          # named nodes (junctions, places) - the previous exact-name behaviour
        nm = attrs.get("name")
        if isinstance(nm, str) and nm.strip():
            try:
                names.setdefault(normalise(nm), []).append((float(attrs["y"]), float(attrs["x"])))
            except (KeyError, TypeError, ValueError):
                continue
    out: dict[str, tuple[float, float]] = {}
    for name, pts in names.items():
        if not name:
            continue
        pts.sort()
        out[name] = pts[len(pts) // 2]          # median point: stays on the road for long/curved roads
    return out


def graph_bbox(graph) -> tuple[float, float, float, float]:
    ys = [float(d["y"]) for _, d in graph.nodes(data=True) if d.get("y") is not None]
    xs = [float(d["x"]) for _, d in graph.nodes(data=True) if d.get("x") is not None]
    return (min(xs), min(ys), max(xs), max(ys))
