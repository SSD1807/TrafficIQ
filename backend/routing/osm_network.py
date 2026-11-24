"""OpenStreetMap road network acquisition with a clearly labelled fallback.

Real graph retrieval requires OSMnx + internet access. Cached GraphML is reused
when available. The fallback graph is deliberately synthetic and must never be
labelled as real OpenStreetMap geometry.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

LOG = logging.getLogger(__name__)
BHUBANESWAR_PLACE = "Bhubaneswar, Khordha, Odisha, India"
BHUBANESWAR_CENTER = (20.2961, 85.8245)  # approximate city-centre reference


class NetworkUnavailableError(Exception):
    """The real OpenStreetMap road network could not be loaded. Carries a user-readable reason and fix hint.

    Raised instead of silently substituting the synthetic demo graph (which must never be shown as the road network).
    """

    def __init__(self, reason: str, hint: str, *, code: str = "road_network_unavailable") -> None:
        super().__init__(reason)
        self.reason, self.hint, self.code = reason, hint, code

    def to_public(self) -> dict[str, str]:
        return {"code": self.code, "message": "The OpenStreetMap road network is not available.", "reason": self.reason, "hint": self.hint}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _is_true(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def build_synthetic_fallback_graph() -> nx.MultiDiGraph:
    """Build an illustrative local graph; coordinates are approximate placeholders."""
    nodes = {
        1: (20.2961, 85.8245, "Fallback Central Junction"),
        2: (20.3015, 85.8205, "Fallback North-West Junction"),
        3: (20.3025, 85.8330, "Fallback North-East Junction"),
        4: (20.2890, 85.8175, "Fallback South-West Junction"),
        5: (20.2875, 85.8325, "Fallback South-East Junction"),
        6: (20.3100, 85.8260, "Fallback North Junction"),
        7: (20.2805, 85.8250, "Fallback South Junction"),
        8: (20.2960, 85.8390, "Fallback East Junction"),
    }
    undirected_edges = [
        (1, 2, 800, "primary"), (1, 3, 950, "primary"),
        (1, 4, 1100, "secondary"), (1, 5, 1050, "secondary"),
        (2, 6, 1050, "secondary"), (3, 6, 980, "primary"),
        (4, 7, 980, "secondary"), (5, 7, 1050, "secondary"),
        (3, 8, 700, "tertiary"), (5, 8, 1250, "tertiary"),
        (2, 4, 1600, "tertiary"), (6, 8, 1700, "tertiary"),
    ]
    G = nx.MultiDiGraph()
    G.graph.update({"name": "Bhubaneswar synthetic fallback", "source": "synthetic_fallback", "crs": "EPSG:4326"})
    for node_id, (lat, lon, label) in nodes.items():
        G.add_node(node_id, y=lat, x=lon, name=label)
    for u, v, length, highway in undirected_edges:
        for a, b in ((u, v), (v, u)):
            G.add_edge(a, b, key=0, length=float(length), highway=highway,
                       maxspeed="35", speed_kph=35.0,
                       travel_time=float(length) / (35_000 / 3600),
                       osmid=f"synthetic-{a}-{b}")
    return G


FALLBACK_RADIUS_M = 9000  # used only if the named-place boundary query fails


class OSMNetworkProvider:
    def __init__(self, cache_path: str | Path = "cache/bhubaneswar_drive.graphml", place: str = BHUBANESWAR_PLACE):
        self.cache_path = Path(cache_path)
        self.place = place
        self.graph: nx.MultiDiGraph | None = None
        self.source = "unloaded"
        self.last_error: str | None = None
        self.loaded_at: str | None = None
        self.query_used: str | None = None

    # ------------------------------------------------------------------ status
    @property
    def has_cache(self) -> bool:
        return self.cache_path.exists()

    @property
    def is_real_osm(self) -> bool:
        return str(self.source).startswith("openstreetmap")

    def load_cached_only(self) -> nx.MultiDiGraph:
        """Never touches the network: cached OSM GraphML if present, else synthetic fallback.

        The dashboard uses this on start-up so a first launch is instant; the user
        triggers the (slow) city download explicitly with ``download()``.
        """
        if self.graph is not None:
            return self.graph
        if self.cache_path.exists():
            try:
                return self._load_from_cache()
            except Exception as exc:  # corrupt cache or osmnx missing
                self.last_error = f"cache unreadable: {type(exc).__name__}: {exc}"
        else:
            self.last_error = "No cached OSM graph yet. Use 'Download Bhubaneswar road network'."
        self.graph = build_synthetic_fallback_graph()
        self.source = "synthetic_fallback"
        self.query_used = None
        return self.graph

    def _load_from_cache(self) -> nx.MultiDiGraph:
        try:
            import osmnx as ox
            G = ox.load_graphml(self.cache_path)
        except ImportError:
            # Same real OSM GraphML, read with NetworkX only. Still genuine OpenStreetMap data, never synthetic.
            from routing.graph_io import load_graphml_networkx
            LOG.warning("osmnx/shapely not importable; reading the cached OSM GraphML with the NetworkX loader")
            ox = None
            G = load_graphml_networkx(self.cache_path)
        G = self._enrich(G, ox)
        self.graph, self.source = G, "openstreetmap_cached"
        self.last_error = None
        self.loaded_at = datetime.fromtimestamp(self.cache_path.stat().st_mtime, timezone.utc).isoformat()
        self.query_used = "graphml cache"
        return G

    def download(self, *, place: str | None = None) -> nx.MultiDiGraph:
        """Download the drivable network from OpenStreetMap (Overpass) and cache it.

        Tries the administrative boundary first, then a radius around the city
        centre. Raises RuntimeError with a readable reason on failure; the
        previously loaded graph (cached or fallback) stays active.
        """
        import osmnx as ox  # ImportError is surfaced to the caller
        ox.settings.use_cache = True
        ox.settings.cache_folder = str(self.cache_path.parent / "osmnx_http_cache")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        G = None
        for label, factory in (
            (f"place:{place or self.place}", lambda: ox.graph_from_place(place or self.place, network_type="drive", simplify=True, retain_all=False)),
            (f"radius:{FALLBACK_RADIUS_M}m around centre", lambda: ox.graph_from_point(BHUBANESWAR_CENTER, dist=FALLBACK_RADIUS_M, network_type="drive", simplify=True, retain_all=False)),
        ):
            try:
                G = factory(); self.query_used = label
                break
            except Exception as exc:
                errors.append(f"{label} -> {type(exc).__name__}: {str(exc)[:160]}")
        if G is None:
            self.last_error = " | ".join(errors)
            raise RuntimeError(f"OpenStreetMap download failed. {self.last_error}")
        ox.save_graphml(G, self.cache_path)
        self.graph = self._enrich(G, ox)
        self.source = "openstreetmap_downloaded"
        self.last_error = None
        self.loaded_at = datetime.now(timezone.utc).isoformat()
        return self.graph

    def load_osm_xml(self, path: str | Path, *, save_to_cache: bool = True) -> nx.MultiDiGraph:
        """Build the graph from a local .osm XML export (offline alternative to Overpass)."""
        import osmnx as ox
        G = ox.graph_from_xml(str(path), simplify=True, retain_all=False)
        if save_to_cache:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            ox.save_graphml(G, self.cache_path)
        self.graph = self._enrich(G, ox)
        self.source = "openstreetmap_file"
        self.query_used = f"local file {Path(path).name}"
        self.last_error = None
        self.loaded_at = datetime.now(timezone.utc).isoformat()
        return self.graph

    def _enrich(self, G, ox):
        if ox is None:
            self._ensure_edge_cost_attributes(G)
            return G
        try:
            G = ox.add_edge_speeds(G)
            G = ox.add_edge_travel_times(G)
        except Exception as exc:
            LOG.warning("OSMnx speed/travel-time enrichment failed; using conservative defaults: %s", exc)
            self._ensure_edge_cost_attributes(G)
        return G

    def require_graph(self, *, download_if_missing: bool = False) -> nx.MultiDiGraph:
        """Return the active road graph or raise NetworkUnavailableError. NEVER substitutes the synthetic fallback.

        Order: already-loaded graph -> cached OSM GraphML -> (only when asked) download from OpenStreetMap.
        A graph injected by tests keeps its own ``source`` label, so a synthetic graph can never be reported as OSM.
        """
        if self.graph is not None:
            return self.graph
        if self.cache_path.exists():
            try:
                return self._load_from_cache()
            except Exception as exc:
                self.last_error = f"cache unreadable: {type(exc).__name__}: {exc}"
                raise NetworkUnavailableError(
                    f"The cached road network file ({self.cache_path.name}) could not be read: {type(exc).__name__}: {exc}",
                    "Delete backend\\cache\\bhubaneswar_drive.graphml and run: scripts\\download_osm.bat (project root)") from exc
        if download_if_missing:
            try:
                return self.download()
            except Exception as exc:
                raise NetworkUnavailableError(str(exc), "Check the internet connection (Overpass/Nominatim), then run: scripts\\download_osm.bat (project root)") from exc
        self.last_error = "No cached OSM graph yet."
        raise NetworkUnavailableError(
            "No cached OpenStreetMap road network exists (backend/cache/bhubaneswar_drive.graphml is missing).",
            "With internet access run: scripts\\download_osm.bat (project root)   (or POST /network/download)", code="road_network_not_downloaded")

    def status(self) -> dict[str, Any]:
        """Loading status for the UI. Reads the cache lazily but never downloads and never fabricates a graph."""
        try:
            self.require_graph()
            state, err = ("ready" if self.is_real_osm else "not_openstreetmap"), None
        except NetworkUnavailableError as exc:
            state, err = "unavailable", exc.to_public()
        out = self.summary()
        out.update(state=state, error=err, cache_path=str(self.cache_path), cache_size_mb=round(self.cache_path.stat().st_size / 1e6, 1) if self.has_cache else 0)
        if self.graph is not None:
            names = out.get("named_edges", 0)
            out["named_edge_share"] = round(names / max(1, out["edges"]), 3)
        return out

    def summary(self) -> dict[str, Any]:
        G = self.graph
        out: dict[str, Any] = {
            "source": self.source, "is_real_osm": self.is_real_osm, "has_cache": self.has_cache,
            "nodes": G.number_of_nodes() if G is not None else 0,
            "edges": G.number_of_edges() if G is not None else 0,
            "loaded_at": self.loaded_at, "query": self.query_used, "last_error": self.last_error,
        }
        if G is not None and G.number_of_edges():
            types: dict[str, int] = {}
            named = oneway = 0
            for _, _, d in G.edges(data=True):
                h = d.get("highway", "unknown"); h = h[0] if isinstance(h, list) else h
                types[str(h)] = types.get(str(h), 0) + 1
                named += bool(d.get("name"))
                oneway += _is_true(d.get("oneway"))
            out.update(road_types=dict(sorted(types.items(), key=lambda kv: -kv[1])),
                       named_edges=named, oneway_edges=oneway)
        return out

    def load(self, force_download: bool = False, allow_fallback: bool = True) -> nx.MultiDiGraph:
        if self.graph is not None and not force_download:
            return self.graph
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if self.cache_path.exists() and not force_download:
            try:
                import osmnx  # noqa: F401
            except ImportError:
                # OSMnx missing: the cached file is still real OSM data, so read it with the NetworkX loader.
                try:
                    return self._load_from_cache()
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
        try:
            import osmnx as ox  # optional runtime dependency
            ox.settings.use_cache = True
            ox.settings.cache_folder = str(self.cache_path.parent / "osmnx_http_cache")
            if self.cache_path.exists() and not force_download:
                G = ox.load_graphml(self.cache_path)
                self.source = "openstreetmap_cached"
            else:
                # network_type='drive' respects one-way driving restrictions in OSM.
                G = ox.graph_from_place(self.place, network_type="drive", simplify=True, retain_all=True)
                ox.save_graphml(G, self.cache_path)
                self.source = "openstreetmap_downloaded"
            try:
                G = ox.add_edge_speeds(G)
                G = ox.add_edge_travel_times(G)
            except Exception as exc:
                LOG.warning("OSMnx speed/travel-time enrichment failed; using conservative defaults: %s", exc)
                self._ensure_edge_cost_attributes(G)
            self.graph = G
            return G
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            LOG.warning("OSM graph unavailable: %s", self.last_error)
            if not allow_fallback:
                raise RuntimeError(f"OpenStreetMap graph unavailable: {self.last_error}") from exc
            self.graph = build_synthetic_fallback_graph()
            self.source = "synthetic_fallback"
            return self.graph

    @staticmethod
    def _ensure_edge_cost_attributes(G: nx.MultiDiGraph) -> None:
        default_speed = {"motorway": 80, "trunk": 65, "primary": 45, "secondary": 35, "tertiary": 30, "residential": 25, "service": 15}
        for u, v, key, data in G.edges(keys=True, data=True):
            length = float(data.get("length", 250.0))
            highway = data.get("highway", "residential")
            if isinstance(highway, list):
                highway = highway[0] if highway else "residential"
            speed = data.get("speed_kph")
            if not speed:
                maxspeed = data.get("maxspeed")
                try:
                    if isinstance(maxspeed, list): maxspeed = maxspeed[0]
                    speed = float(str(maxspeed).split()[0]) if maxspeed else default_speed.get(str(highway), 25)
                except (TypeError, ValueError):
                    speed = default_speed.get(str(highway), 25)
            speed = max(5.0, float(speed))
            data["speed_kph"] = speed
            data["travel_time"] = length / (speed * 1000 / 3600)
