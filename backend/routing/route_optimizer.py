
"""Dijkstra/A* route optimization on directed OSMnx/NetworkX road graphs."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import networkx as nx

from routing.impact_rules import get_rules
from routing.osm_network import haversine_m


SPEED_BY_HIGHWAY = {
    "motorway": 80.0,
    "trunk": 65.0,
    "primary": 45.0,
    "secondary": 35.0,
    "tertiary": 30.0,
    "unclassified": 25.0,
    "residential": 25.0,
    "living_street": 10.0,
    "service": 15.0,
}

CONGESTION_MULTIPLIER = {
    "low": 1.0,
    "medium": 1.25,
    "high": 1.75,
}


def _first(value, default=None):
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return value if value is not None else default


def _edge_speed_kph(data: dict[str, Any]) -> float:
    speed = _first(data.get("speed_kph"))

    if speed is None:
        raw = _first(data.get("maxspeed"))

        try:
            speed = (
                float(str(raw).split()[0])
                if raw is not None
                else None
            )
        except ValueError:
            speed = None

    if speed is None:
        highway = str(
            _first(data.get("highway"), "residential")
        )
        speed = SPEED_BY_HIGHWAY.get(highway, 25.0)

    return max(5.0, min(float(speed), 130.0))


def _road_name(data: dict[str, Any]) -> str | None:
    name = _first(data.get("name"))
    return str(name) if name else None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {
            "true", "yes", "1"
        }
    return bool(value)


def _geometry_latlon(
    data: dict[str, Any],
    G: nx.MultiDiGraph | nx.DiGraph,
    u,
    v,
) -> list[list[float]]:

    geom = data.get("geometry")

    if geom is not None and hasattr(geom, "coords"):
        return [
            [float(y), float(x)]
            for x, y in geom.coords
        ]

    a, b = G.nodes[u], G.nodes[v]

    return [
        [
            float(a.get("y", 0)),
            float(a.get("x", 0)),
        ],
        [
            float(b.get("y", 0)),
            float(b.get("x", 0)),
        ],
    ]


_GEOCODERS: dict = {}


class RouteOptimizer:

    def __init__(
        self,
        graph: nx.Graph,
        graph_source: str = "unknown",
        incident_manager=None,
    ):

        if graph.number_of_nodes() < 2:
            raise ValueError(
                "Road graph must contain at least two nodes"
            )

        self.graph = graph
        self.graph_source = (
            graph_source
            or graph.graph.get("source", "openstreetmap")
        )
        self.incident_manager = incident_manager
        self.impact_rules = get_rules()

        # Calculate graph boundaries once.
        coordinates = [
            (float(data["y"]), float(data["x"]))
            for _, data in graph.nodes(data=True)
            if data.get("y") is not None
            and data.get("x") is not None
        ]

        if not coordinates:
            raise ValueError(
                "Road graph does not contain valid coordinates"
            )

        self.min_lat = min(p[0] for p in coordinates)
        self.max_lat = max(p[0] for p in coordinates)
        self.min_lon = min(p[1] for p in coordinates)
        self.max_lon = max(p[1] for p in coordinates)

    # ---------------------------------------------------------
    # GEOCODING
    # ---------------------------------------------------------

    def _validate_geocoded_coordinates(
        self,
        latitude: float,
        longitude: float,
        query: str,
    ):
        """Reject coordinates outside the active road graph."""

        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):
            raise ValueError(
                f"Invalid coordinates returned for '{query}'"
            )

        # Allow a small margin around the graph boundary.
        margin = 0.02

        inside_graph_bounds = (
            self.min_lat - margin
            <= latitude
            <= self.max_lat + margin
            and self.min_lon - margin
            <= longitude
            <= self.max_lon + margin
        )

        if not inside_graph_bounds:
            raise ValueError(
                f"Location '{query}' resolved outside the active "
                f"road network. Returned coordinates: "
                f"({latitude}, {longitude}). "
                f"Please provide a more specific Bhubaneswar location."
            )

        return latitude, longitude

    def geocoder(self):
        """Shared Geocoder for the loaded graph (local road-name index + Nominatim), created once per graph."""
        from routing.geocoding import Geocoder, graph_bbox, road_name_index
        key = id(self.graph)
        gc = _GEOCODERS.get(key)
        if gc is None:
            cache = Path(__file__).resolve().parents[1] / "cache" / "nominatim_cache.json"
            gc = Geocoder(bbox=graph_bbox(self.graph), road_names=road_name_index(self.graph), cache_path=cache,
                          enabled=os.getenv("NOMINATIM_ENABLED", "true").strip().lower() not in {"0", "false", "no"})
            _GEOCODERS.clear()          # keep a single entry: the active graph
            _GEOCODERS[key] = gc
        return gc

    def geocode_detail(self, query: str):
        """Full GeocodeResult (source, display name, attempts). Raises routing.geocoding.GeocodeError (a ValueError)."""
        result = self.geocoder().geocode(query)
        self._validate_geocoded_coordinates(result.latitude, result.longitude, query)
        return result

    def geocode_place(self, query: str):
        """Resolve a place name to (lat, lon) inside the loaded road graph.

        Uses the road names of the loaded OSM graph first, then Nominatim restricted to India and to the graph's
        bounding box, including spelling variants. Failures raise GeocodeError (typed, readable); see routing/geocoding.py.
        """
        result = self.geocode_detail(query)
        return result.latitude, result.longitude

    # ---------------------------------------------------------
    # NEAREST ROAD NODE
    # ---------------------------------------------------------

    def nearest_node(
        self,
        latitude: float,
        longitude: float,
    ):

        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):
            raise ValueError(
                "Invalid latitude/longitude"
            )

        # Do not allow locations outside the graph boundary.
        self._validate_geocoded_coordinates(
            latitude,
            longitude,
            "Route coordinate",
        )

        try:
            import osmnx as ox

            node = ox.distance.nearest_nodes(
                self.graph,
                X=longitude,
                Y=latitude,
            )

            return node

        except ImportError:
            pass

        except Exception:
            # Fall back to haversine search if OSMnx's
            # spatial index is unavailable.
            pass

        return min(
            self.graph.nodes,
            key=lambda n: haversine_m(
                latitude,
                longitude,
                float(self.graph.nodes[n].get("y", 0)),
                float(self.graph.nodes[n].get("x", 0)),
            ),
        )

    # ---------------------------------------------------------
    # ROUTING GRAPH
    # ---------------------------------------------------------

    def _routing_view(
        self,
        congestion_by_edge: dict | None = None,
        rainfall_mm: float = 0.0,
        apply_weather: bool = True,
        measured_by_edge: dict | None = None,
    ) -> nx.DiGraph:

        view = nx.DiGraph()

        view.graph.update(self.graph.graph)

        view.add_nodes_from(
            (n, dict(d))
            for n, d in self.graph.nodes(data=True)
        )

        congestion_by_edge = congestion_by_edge or {}
        measured_by_edge = measured_by_edge or {}

        incidents = (
            self.incident_manager.edge_effects()
            if self.incident_manager
            else {}
        )

        for u, v, edge_data in self.graph.edges(data=True):

            view_candidates = [(None, edge_data)]

            if isinstance(
                self.graph,
                (nx.MultiDiGraph, nx.MultiGraph),
            ):
                view_candidates = list(
                    self.graph.get_edge_data(u, v).items()
                )

            best = None

            for key, data in view_candidates:

                # Incidents may target one specific parallel edge (edge_key); each incident id counts once per edge so
                # a polled provider incident can never be applied twice.
                incident_list = []
                _seen_incident_ids = set()
                for _inc in incidents.get((str(u), str(v)), []):
                    _ek = _inc.get("edge_key")
                    if _ek is not None and key is not None and str(_ek) != str(key):
                        continue
                    if _inc["id"] in _seen_incident_ids:
                        continue
                    _seen_incident_ids.add(_inc["id"])
                    incident_list.append(_inc)

                # Impact rules (routing/impact_rules.py) decide per incident whether an edge is blocked or priced.
                incident_effects = [(_i, self.impact_rules.edge_effect(_i)) for _i in incident_list]
                if any(_e["effect"] == "block" for _, _e in incident_effects):
                    continue

                length = max(
                    1.0,
                    float(data.get("length", 250.0)),
                )

                speed = _edge_speed_kph(data)

                base_seconds = length / (
                    speed * 1000 / 3600
                )

                cong = congestion_by_edge.get(
                    (u, v),
                    congestion_by_edge.get(
                        f"{u}->{v}",
                        congestion_by_edge.get(
                            str(u), "low"
                        ),
                    ),
                )

                if isinstance(cong, int):
                    cong = {
                        0: "low",
                        1: "medium",
                        2: "high",
                    }.get(cong, "low")

                congestion_factor = (
                    CONGESTION_MULTIPLIER.get(
                        str(cong).lower(), 1.0
                    )
                )

                traffic_basis = "model"

                measured = measured_by_edge.get(
                    (u, v)
                )

                if measured is not None:

                    if measured.get("road_closure"):
                        continue

                    congestion_factor = float(
                        measured["factor"]
                    )

                    cong = "measured"
                    traffic_basis = "tomtom_measured"

                weather_factor = (
                    1.0
                    + min(
                        max(float(rainfall_mm), 0.0),
                        30.0,
                    ) * 0.012
                    if apply_weather
                    else 1.0
                )

                incident_factor = 1.0
                incident_delay = 0.0

                for incident, effect in incident_effects:
                    # A reported jam is congestion. Where measured TomTom flow already prices this edge, applying it
                    # again would double count, so it is skipped there.
                    if incident["kind"] == "traffic_jam" and measured is not None:
                        continue
                    incident_factor *= effect["cost_factor"]
                    incident_delay += effect["delay_s"]

                travel_seconds = (
                    base_seconds
                    * congestion_factor
                    * weather_factor
                    * incident_factor
                    + incident_delay
                )

                candidate = {
                    "weight": travel_seconds,
                    "travel_time_sec": travel_seconds,
                    "base_travel_time_sec": base_seconds,
                    "length": length,
                    "speed_kph": speed,
                    "congestion": str(cong).lower(),
                    "traffic_basis": traffic_basis,
                    "measured_speed_kph": (
                        measured.get("current_kph")
                        if measured is not None
                        else None
                    ),
                    "weather_factor": weather_factor,
                    "incident_ids": [
                        i["id"] for i in incident_list
                    ],
                    "incident_delay_s": incident_delay,
                    "geometry": data.get("geometry"),
                    "edge_key": key,
                    "highway": _first(
                        data.get("highway"), "unknown"
                    ),
                    "osmid": data.get("osmid"),
                    "name": _road_name(data),
                    "oneway": _truthy(
                        data.get("oneway")
                    ),
                }

                if (
                    best is None
                    or candidate["weight"] < best["weight"]
                ):
                    best = candidate

            if best is not None:

                if view.has_edge(u, v):

                    if best["weight"] < view[u][v]["weight"]:
                        view[u][v].update(best)

                else:
                    view.add_edge(u, v, **best)

        return view

    # ---------------------------------------------------------
    # ROUTE RESULT FORMATTING
    # ---------------------------------------------------------

    def _path_result(
        self,
        view: nx.DiGraph,
        path: list,
        algorithm: str,
        rank: int,
    ) -> dict[str, Any]:

        distance = 0.0
        travel_time = 0.0
        base_time = 0.0

        segments = []
        polyline: list[list[float]] = []

        for u, v in zip(path[:-1], path[1:]):

            edge = view[u][v]

            distance += float(edge["length"])
            travel_time += float(
                edge["travel_time_sec"]
            )
            base_time += float(
                edge["base_travel_time_sec"]
            )

            points = _geometry_latlon(
                edge, view, u, v
            )

            if (
                polyline
                and points
                and polyline[-1] == points[0]
            ):
                points = points[1:]

            polyline.extend(points)

            segments.append({
                "from_node": str(u),
                "to_node": str(v),
                "length_m": round(
                    float(edge["length"]), 1
                ),
                "estimated_speed_kph": round(
                    float(edge["speed_kph"]), 1
                ),
                "congestion": edge["congestion"],
                "travel_time_min": round(
                    float(edge["travel_time_sec"]) / 60,
                    2,
                ),
                "incident_ids": edge["incident_ids"],
                "highway": str(edge["highway"]),
                "road_name": edge.get("name"),
                "oneway": bool(edge.get("oneway")),
                "traffic_basis": edge.get(
                    "traffic_basis", "model"
                ),
                "measured_speed_kph": edge.get(
                    "measured_speed_kph"
                ),
            })

        measured_len = sum(
            sg["length_m"]
            for sg in segments
            if sg["traffic_basis"] == "tomtom_measured"
        )

        steps: list[dict[str, Any]] = []

        for seg in segments:

            label = (
                seg["road_name"]
                or (
                    "Unnamed "
                    + seg["highway"].replace("_", " ")
                    + " road"
                )
            )

            if steps and steps[-1]["road"] == label:

                steps[-1]["distance_m"] += (
                    seg["length_m"]
                )

                steps[-1]["time_min"] += (
                    seg["travel_time_min"]
                )

            else:

                steps.append({
                    "road": label,
                    "highway": seg["highway"],
                    "distance_m": seg["length_m"],
                    "time_min": seg["travel_time_min"],
                    "congestion": seg["congestion"],
                    "oneway": seg["oneway"],
                })

        for st in steps:
            st["distance_m"] = round(
                st["distance_m"], 1
            )
            st["time_min"] = round(
                st["time_min"], 2
            )

        via: dict[str, float] = {}

        for st in steps:

            if not st["road"].startswith("Unnamed"):

                via[st["road"]] = (
                    via.get(st["road"], 0.0)
                    + st["distance_m"]
                )

        return {
            "rank": rank,
            "algorithm": algorithm,
            "steps": steps,
            "via_roads": [
                k
                for k, _ in sorted(
                    via.items(),
                    key=lambda kv: -kv[1],
                )[:3]
            ],
            "node_sequence": [
                str(n) for n in path
            ],
            "distance_km": round(
                distance / 1000, 3
            ),
            "estimated_travel_time_min": round(
                travel_time / 60, 2
            ),
            "free_flow_estimate_min": round(
                base_time / 60, 2
            ),
            "delay_vs_free_flow_min": round(
                max(
                    0.0,
                    (travel_time - base_time) / 60,
                ),
                2,
            ),
            "measured_segment_count": sum(
                sg["traffic_basis"] == "tomtom_measured"
                for sg in segments
            ),
            "measured_distance_share": (
                round(measured_len / distance, 3)
                if distance
                else 0.0
            ),
            "segments": segments,
            "polyline_latlon": polyline,
            "graph_source": self.graph_source,
            "is_real_road_geometry": (
                self.graph_source.startswith(
                    "openstreetmap"
                )
            ),
        }

    # ---------------------------------------------------------
    # ROUTE CALCULATION
    # ---------------------------------------------------------

    def route_nodes(
        self,
        origin_node,
        destination_node,
        *,
        algorithm: str = "dijkstra",
        alternatives: int = 3,
        congestion_by_edge: dict | None = None,
        rainfall_mm: float = 0.0,
        measured_by_edge: dict | None = None,
        prebuilt_view: nx.DiGraph | None = None,
    ) -> dict[str, Any]:

        if (
            origin_node not in self.graph
            or destination_node not in self.graph
        ):
            raise ValueError(
                "Origin or destination node does not exist "
                "in the active road graph"
            )

        if origin_node == destination_node:
            raise ValueError(
                "Origin and destination resolve to "
                "the same road node"
            )

        view = prebuilt_view if prebuilt_view is not None else self._routing_view(
            congestion_by_edge,
            rainfall_mm,
            measured_by_edge=measured_by_edge,
        )

        if not nx.has_path(
            view,
            origin_node,
            destination_node,
        ):
            raise nx.NetworkXNoPath(
                "No drivable route connects the selected points"
            )

        algo = algorithm.lower().strip()

        if algo not in {
            "dijkstra",
            "astar",
            "a_star",
        }:
            raise ValueError(
                "algorithm must be 'dijkstra' or 'astar'"
            )

        if algo == "dijkstra":

            best_path = nx.dijkstra_path(
                view,
                origin_node,
                destination_node,
                weight="weight",
            )

        else:

            def heuristic(node, target):

                a, b = (
                    view.nodes[node],
                    view.nodes[target],
                )

                distance = haversine_m(
                    float(a.get("y", 0)),
                    float(a.get("x", 0)),
                    float(b.get("y", 0)),
                    float(b.get("x", 0)),
                )

                # Maximum plausible speed gives
                # a conservative lower-bound heuristic.
                return distance / (130_000 / 3600)

            best_path = nx.astar_path(
                view,
                origin_node,
                destination_node,
                heuristic=heuristic,
                weight="weight",
            )

        k = max(
            1,
            min(int(alternatives), 5),
        )

        paths = [best_path]

        if k > 1:

            try:

                iterator = nx.shortest_simple_paths(
                    view,
                    origin_node,
                    destination_node,
                    weight="weight",
                )

                paths = []

                for path in iterator:

                    paths.append(path)

                    if len(paths) >= k:
                        break

            except (
                nx.NetworkXNoPath,
                nx.NetworkXNotImplemented,
            ):
                paths = [best_path]

        routes = [
            self._path_result(
                view,
                path,
                algo,
                i + 1,
            )
            for i, path in enumerate(paths)
        ]

        return {
            "origin_node": str(origin_node),
            "destination_node": str(destination_node),
            "algorithm": algo,
            "graph_source": self.graph_source,
            "route_count": len(routes),
            "routes": routes,
            "active_incident_count": (
                len(self.incident_manager.list_active())
                if self.incident_manager
                else 0
            ),
        }

    # ---------------------------------------------------------
    # COORDINATE-BASED ROUTING
    # ---------------------------------------------------------

    def route_coordinates(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        **kwargs,
    ):

        origin_node = self.nearest_node(
            *origin
        )

        destination_node = self.nearest_node(
            *destination
        )

        return self.route_nodes(
            origin_node,
            destination_node,
            **kwargs,
        )
