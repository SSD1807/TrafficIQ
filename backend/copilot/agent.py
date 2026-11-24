
"""Traffic Copilot for the Bhubaneswar Traffic Bottleneck Prediction
and Smart Routing System.

Features:
- Ollama tool calling
- Optional OpenAI provider
- Rule-based fallback
- ML traffic predictions
- 30-minute and 60-minute forecasts
- Dijkstra and A* route optimization
- TomTom traffic flow and incidents
- Open-Meteo weather
- Application-recorded incidents
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable

import httpx

from simulation.traffic_simulator import TrafficSimulator
from copilot import incident_answers
from routing.incident_impact import compute_incident_impact


class TrafficCopilot:

    def __init__(
        self,
        traffic_service,
        forecast_service,
        weather_service,
        route_optimizer_factory: Callable[[], Any],
        incident_manager,
        tomtom_service=None,
        incident_sync=None,
    ):
        self.traffic = traffic_service
        self.forecasts = forecast_service
        self.weather = weather_service
        self.route_optimizer_factory = route_optimizer_factory
        self.incidents = incident_manager
        self.tomtom = tomtom_service
        self.incident_sync = incident_sync
        # Last route the user asked about; lets "my route" / "find an alternative route" follow-ups work. Not persisted.
        self._last_route: tuple[str, str, str] | None = None

        self.ollama_url = os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        ).rstrip("/")

        self.ollama_model = os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5:3b",
        )

    # =========================================================
    # INTENT DETECTION
    # =========================================================

    @staticmethod
    def _is_tomtom_incident_request(message: str) -> bool:
        msg = message.casefold()

        incident_terms = (
            "incident",
            "incidents",
            "accident",
            "accidents",
            "closure",
            "closures",
            "roadwork",
            "roadworks",
        )

        has_incident_term = any(
            term in msg for term in incident_terms
        )

        return (
            ("tomtom" in msg and has_incident_term)
            or "live incident" in msg
            or "live incidents" in msg
        )

    @staticmethod
    def _is_incident_intent(message: str) -> bool:
        """Incident questions (any phrasing) and route-follow-ups about incidents / alternatives."""
        return (incident_answers.is_incident_query(message)
                or TrafficCopilot._is_tomtom_incident_request(message)
                or incident_answers.is_route_context_query(message))

    @staticmethod
    def _is_forecast_request(message: str) -> bool:
        msg = message.casefold()

        forecast_terms = (
            "forecast",
            "forecasts",
            "next 30",
            "next 60",
            "30-minute",
            "60-minute",
            "30 minute",
            "60 minute",
            "half-hour forecast",
            "hourly forecast",
        )

        return any(
            term in msg for term in forecast_terms
        )

    @staticmethod
    def _is_prediction_request(message: str) -> bool:
        """
        Detect explicit requests for ML predictions.

        Do not classify generic questions containing words such as
        'bottleneck' or 'congestion' as ML prediction requests.
        """

        msg = message.casefold()

        prediction_phrases = (
            "traffic prediction",
            "traffic predictions",
            "predict traffic",
            "predicted traffic",
            "traffic is predicted",
            "show predictions",
            "show prediction",
            "show me predictions",
            "show me prediction",
            "show traffic predictions",
            "show me traffic predictions",
            "ml prediction",
            "ml predictions",
            "machine learning prediction",
            "machine learning predictions",
            "which locations have predicted",
            "which areas have predicted",
            "which locations are predicted",
            "which areas are predicted",
            "where is congestion predicted",
            "where will congestion occur",
            "predict congestion",
            "congestion prediction",
            "congestion predictions",
            "bottleneck prediction",
            "bottleneck predictions",
            "high congestion locations",
            "locations with high congestion",
            "areas with high congestion",
        )

        return any(
            phrase in msg
            for phrase in prediction_phrases
        )

    @staticmethod
    def _extract_route_request(message: str):
        """
        Extract origin, destination and routing algorithm.

        Examples:
        - Route from KIIT University to Patia
        - Find the best route from KIIT University to Patia using A*
        - Find a route from Rasulgarh to Airport Road using Dijkstra
        """

        match = re.search(
            r"\bfrom\s+(.+?)\s+to\s+(.+?)"
            r"(?:\s+using\s+(a\s*\*|a-star|astar|dijkstra))?"
            r"\s*[?.!,]*\s*$",
            message,
            re.IGNORECASE,
        )

        if not match:
            return None

        route_keywords = (
            "route",
            "directions",
            "travel",
            "drive",
            "go",
            "path",
            "shortest",
        )

        if not any(
            keyword in message.casefold()
            for keyword in route_keywords
        ):
            return None

        origin = match.group(1).strip(" ,")
        destination = match.group(2).strip(" ,")

        requested_algorithm = (
            match.group(3) or ""
        ).casefold().strip()

        if not origin or not destination:
            return None

        algorithm = (
            "astar"
            if requested_algorithm in (
                "a*",
                "a *",
                "a-star",
                "astar",
            )
            else "dijkstra"
        )

        return origin, destination, algorithm

    # =========================================================
    # BACKEND TOOLS
    # =========================================================

    def _tool_functions(self) -> dict[str, Callable[..., Any]]:

        def get_weather():
            return self.weather.get_current(
                allow_simulated_fallback=False
            )

        def get_traffic_predictions():
            start = time.perf_counter()

            print(
                "[ML DEBUG] Starting traffic predictions.",
                flush=True,
            )

            predictions = self.traffic.predict_all()

            print(
                "[ML DEBUG] Predictions completed in "
                f"{time.perf_counter() - start:.2f}s",
                flush=True,
            )

            print(
                f"[ML DEBUG] Number of predictions: "
                f"{len(predictions)}",
                flush=True,
            )

            return predictions

        def get_forecast(
            location: str,
            horizon_minutes: int = 30,
        ):
            if horizon_minutes not in (30, 60):
                raise ValueError(
                    "Forecast horizon must be 30 or 60 minutes."
                )

            start = time.perf_counter()

            print(
                f"[FORECAST DEBUG] Starting "
                f"{horizon_minutes}-minute forecast for {location}.",
                flush=True,
            )

            result = self.forecasts.forecast(
                location,
                horizon_minutes,
            )

            print(
                "[FORECAST DEBUG] Forecast completed in "
                f"{time.perf_counter() - start:.2f}s",
                flush=True,
            )

            return result

        def list_incidents():
            return self.incidents.list_active()

        def plan_route(
            origin: str,
            destination: str,
            algorithm: str = "dijkstra",
            departure_time: str | None = None,
        ):
            total_start = time.perf_counter()

            print(
                "[ROUTE DEBUG] Starting route calculation.",
                flush=True,
            )

            print(
                f"[ROUTE DEBUG] Origin: {origin}",
                flush=True,
            )

            print(
                f"[ROUTE DEBUG] Destination: {destination}",
                flush=True,
            )

            print(
                f"[ROUTE DEBUG] Algorithm: {algorithm}",
                flush=True,
            )

            step_start = time.perf_counter()

            optimizer = self.route_optimizer_factory()

            print(
                "[ROUTE DEBUG] Optimizer initialized in "
                f"{time.perf_counter() - step_start:.2f}s",
                flush=True,
            )

            step_start = time.perf_counter()

            origin_coords = optimizer.geocode_place(origin)

            print(
                f"[ROUTE DEBUG] Origin geocoded: {origin_coords} "
                f"in {time.perf_counter() - step_start:.2f}s",
                flush=True,
            )

            step_start = time.perf_counter()

            destination_coords = optimizer.geocode_place(
                destination
            )

            print(
                f"[ROUTE DEBUG] Destination geocoded: "
                f"{destination_coords} "
                f"in {time.perf_counter() - step_start:.2f}s",
                flush=True,
            )

            congestion = None
            departure = None

            if departure_time:
                departure = datetime.fromisoformat(
                    departure_time
                )

                if departure.tzinfo is None:
                    departure = departure.replace(
                        tzinfo=ZoneInfo("Asia/Kolkata")
                    )

                congestion = TrafficSimulator().edge_conditions(
                    optimizer.graph,
                    departure,
                )

            step_start = time.perf_counter()

            result = optimizer.route_coordinates(
                origin_coords,
                destination_coords,
                algorithm=algorithm,
                alternatives=3,
                congestion_by_edge=congestion,
            )

            print(
                "[ROUTE DEBUG] Route calculation completed in "
                f"{time.perf_counter() - step_start:.2f}s",
                flush=True,
            )

            result["departure_time"] = (
                departure.isoformat()
                if departure
                else None
            )

            result["traffic_cost_mode"] = (
                "simulated_departure_time_traffic"
                if departure
                else "free_flow_road_attributes_plus_incidents"
            )

            result["origin_name"] = origin
            result["destination_name"] = destination
            result["algorithm"] = algorithm

            print(
                "[ROUTE DEBUG] TOTAL: "
                f"{time.perf_counter() - total_start:.2f}s",
                flush=True,
            )

            return result

        def get_tomtom_flow(place: str):

            if self.tomtom is None:
                return {
                    "status": "unavailable",
                    "is_live": False,
                    "message": "TomTom service is not connected.",
                }

            optimizer = self.route_optimizer_factory()

            latitude, longitude = optimizer.geocode_place(
                place
            )

            result = self.tomtom.flow_at(
                latitude,
                longitude,
            )

            if result.get("status") != "ok":
                return result

            flow = (
                result.get("data", {})
                .get("flowSegmentData", {})
            )

            current_speed = flow.get("currentSpeed")
            free_flow_speed = flow.get("freeFlowSpeed")

            speed_reduction_pct = None

            if (
                isinstance(current_speed, (int, float))
                and isinstance(free_flow_speed, (int, float))
                and free_flow_speed > 0
            ):
                speed_reduction_pct = round(
                    max(
                        0,
                        (
                            1 - current_speed / free_flow_speed
                        ) * 100,
                    ),
                    2,
                )

            return {
                "status": result.get("status"),
                "source": result.get("source"),
                "is_live": result.get("is_live"),
                "cache_status": result.get("cache_status"),
                "fetched_at": result.get("fetched_at"),
                "place": place,
                "latitude": latitude,
                "longitude": longitude,
                "current_speed_kph": current_speed,
                "free_flow_speed_kph": free_flow_speed,
                "speed_reduction_pct": speed_reduction_pct,
                "current_travel_time_seconds": flow.get(
                    "currentTravelTime"
                ),
                "free_flow_travel_time_seconds": flow.get(
                    "freeFlowTravelTime"
                ),
                "confidence": flow.get("confidence"),
                "road_closure": flow.get("roadClosure"),
                "road_name": flow.get("frc"),
            }

        def get_live_incidents(refresh: bool = True):
            """Incident feed (TomTom live + manual/simulated, each labelled) with the provider's real status."""
            if self.incident_sync is None:
                return {
                    "provider": {"state": "unavailable", "is_live": False,
                                 "message": "The incident service is not connected to the Copilot."},
                    "incidents": [m for m in (self.incidents.feed() if self.incidents is not None else [])],
                    "counts": {},
                }
            if refresh:
                self.incident_sync.refresh()  # rate limited server-side; failures keep cached data
            return {
                "provider": self.incident_sync.provider.status(),
                "incidents": self.incident_sync.manager.feed(),
                "counts": self.incident_sync.manager.counts(),
            }

        def get_closed_roads():
            data = get_live_incidents()
            data["incidents"] = [e for e in data["incidents"] if e["kind"] == "road_closure"]
            return data

        def check_route_incidents(origin: str, destination: str, algorithm: str = "dijkstra"):
            optimizer = self.route_optimizer_factory()
            start = optimizer.nearest_node(*optimizer.geocode_place(origin))
            end = optimizer.nearest_node(*optimizer.geocode_place(destination))
            if self.incident_sync is not None:
                self.incident_sync.refresh()
            impact = compute_incident_impact(optimizer, start, end, algorithm=algorithm, alternatives=3)
            impact["origin_name"], impact["destination_name"], impact["algorithm"] = origin, destination, algorithm
            impact["provider"] = (self.incident_sync.provider.status() if self.incident_sync is not None
                                  else {"state": "unavailable", "is_live": False, "message": "incident service not connected"})
            return impact

        def get_tomtom_incidents():
            # Backwards-compatible tool name: now the normalised, road-matched TomTom feed (never fabricated).
            data = get_live_incidents()
            data["incidents"] = [e for e in data["incidents"] if e["source"] == "tomtom"]
            return data

        return {
            "get_weather": get_weather,
            "get_traffic_predictions": get_traffic_predictions,
            "get_forecast": get_forecast,
            "list_incidents": list_incidents,
            "plan_route": plan_route,
            "get_tomtom_flow": get_tomtom_flow,
            "get_tomtom_incidents": get_tomtom_incidents,
            "get_live_incidents": get_live_incidents,
            "get_closed_roads": get_closed_roads,
            "check_route_incidents": check_route_incidents,
        }

    # =========================================================
    # OLLAMA TOOL DEFINITIONS
    # =========================================================

    def _ollama_tools(self):

        def tool(name, description, properties, required):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }

        return [
            tool(
                "get_weather",
                "Retrieve current Open-Meteo weather for Bhubaneswar.",
                {},
                [],
            ),
            tool(
                "get_traffic_predictions",
                "Retrieve Phase 1 ML predictions based on historical synthetic traffic data. Never describe these as live traffic.",
                {},
                [],
            ),
            tool(
                "get_forecast",
                "Retrieve a trained 30 or 60 minute forecast for a configured synthetic traffic location.",
                {
                    "location": {"type": "string"},
                    "horizon_minutes": {
                        "type": "integer",
                        "enum": [30, 60],
                    },
                },
                ["location", "horizon_minutes"],
            ),
            tool(
                "list_incidents",
                "List active incidents recorded in the application.",
                {},
                [],
            ),
            tool(
                "plan_route",
                "Calculate a route between two named places using the road graph.",
                {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "algorithm": {
                        "type": "string",
                        "enum": ["dijkstra", "astar"],
                    },
                    "departure_time": {
                        "type": ["string", "null"]
                    },
                },
                [
                    "origin",
                    "destination",
                    "algorithm",
                    "departure_time",
                ],
            ),
            tool(
                "get_tomtom_flow",
                "Retrieve live or cached TomTom traffic flow for a named Bhubaneswar place.",
                {
                    "place": {"type": "string"},
                },
                ["place"],
            ),
            tool(
                "get_tomtom_incidents",
                "Retrieve live TomTom traffic incidents (matched to roads) for Bhubaneswar, with the provider's real status.",
                {},
                [],
            ),
            tool(
                "get_live_incidents",
                "Latest incident feed: live TomTom incidents plus manual/simulated test incidents, each labelled by source.",
                {},
                [],
            ),
            tool(
                "get_closed_roads",
                "List roads currently reported closed (TomTom live and manual/simulated, labelled by source).",
                {},
                [],
            ),
            tool(
                "check_route_incidents",
                "Check whether incidents affect the route between two places and return the alternative route.",
                {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "algorithm": {"type": "string", "enum": ["dijkstra", "astar"]},
                },
                ["origin", "destination", "algorithm"],
            ),
        ]

    # =========================================================
    # RULE-BASED FALLBACK AND DIRECT DATA RESPONSES
    # =========================================================

    def _incident_response(self, message: str, functions: dict[str, Callable[..., Any]]) -> dict[str, Any]:
        """Answer incident questions strictly from tool results (live TomTom, manual, simulated, route impact)."""
        focus = incident_answers.focus_of(message)
        route = self._extract_route_request(message)
        wants_route = bool(route) or incident_answers.is_route_context_query(message)
        try:
            if wants_route:
                if route:
                    self._last_route = route
                if self._last_route is None:
                    data = functions["get_live_incidents"]()
                    return {
                        "answer": ("I do not have a selected route in this chat yet. Ask for one first (for example "
                                   "'route from KIIT University to Patia') or say 'incidents on the route from A to B'.\n"
                                   + incident_answers.describe_incidents(data, focus)),
                        "tool_results": {"incident_feed": data},
                        "mode": "rule_based",
                    }
                origin, destination, algorithm = self._last_route
                impact = functions["check_route_incidents"](origin, destination, algorithm)
                return {
                    "answer": incident_answers.describe_route_impact(origin, destination, impact, impact["provider"]),
                    "tool_results": {"route_incident_impact": impact},
                    "mode": "rule_based",
                }
            data = functions["get_closed_roads"]() if focus == "closures" else functions["get_live_incidents"]()
            return {
                "answer": incident_answers.describe_incidents(data, focus),
                "tool_results": {"incident_feed": data},
                "mode": "rule_based",
            }
        except Exception as exc:
            return {
                "answer": f"Incident retrieval failed: {type(exc).__name__}: {exc}",
                "tool_results": {},
                "mode": "rule_based",
            }

    def _rule_based(self, message: str) -> dict[str, Any]:

        msg = message.casefold()
        functions = self._tool_functions()

        # -----------------------------------------------------
        # GREETINGS
        # -----------------------------------------------------

        if re.fullmatch(
            r"\s*(hi|hello|hey|good morning|good afternoon|"
            r"good evening|thanks|thank you)[!. ]*\s*",
            msg,
        ):
            return {
                "answer": (
                    "Hello! I'm your Bhubaneswar Traffic Copilot. "
                    "I can check TomTom traffic, weather, recorded "
                    "incidents, ML predictions, forecasts and routes. "
                    "What would you like to check?"
                ),
                "tool_results": {},
                "mode": "rule_based",
            }

        # -----------------------------------------------------
        # INCIDENTS (live TomTom + manual/simulated, route impact)
        # -----------------------------------------------------

        if self._is_incident_intent(message):
            return self._incident_response(message, functions)

        # -----------------------------------------------------
        # ROUTING
        # -----------------------------------------------------

        route_request = self._extract_route_request(message)

        if route_request:
            self._last_route = (route_request[0], route_request[1], route_request[2])

            origin, destination, algorithm = route_request

            try:
                result = functions["plan_route"](
                    origin=origin,
                    destination=destination,
                    algorithm=algorithm,
                )

                routes = result.get("routes", [])

                if not routes:
                    return {
                        "answer": (
                            f"No route was found from {origin} "
                            f"to {destination}."
                        ),
                        "tool_results": {"route": result},
                        "mode": "rule_based",
                    }

                best = routes[0]

                distance = best.get("distance_km")

                travel_time = best.get(
                    "estimated_travel_time_min"
                )

                alternative_count = max(
                    0,
                    len(routes) - 1,
                )

                answer = (
                    f"Route calculated successfully from "
                    f"{origin} to {destination} using "
                    f"{algorithm.upper()}.\n\n"
                    f"**Best route**\n"
                    f"Distance: {distance} km\n"
                    f"Estimated travel time: {travel_time} minutes\n"
                    f"Alternative routes available: {alternative_count}\n"
                    f"Traffic cost mode: "
                    f"{result.get('traffic_cost_mode', 'Road attributes and incidents')}"
                )

                return {
                    "answer": answer,
                    "tool_results": {
                        "route": result
                    },
                    "mode": "rule_based",
                }

            except Exception as exc:
                print(
                    f"[ROUTE ERROR] {type(exc).__name__}: {exc}",
                    flush=True,
                )

                return {
                    "answer": (
                        "Route calculation failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "tool_results": {},
                    "mode": "rule_based",
                }

        # -----------------------------------------------------
        # LIVE TOMTOM TRAFFIC FLOW
        # -----------------------------------------------------

        if any(k in msg for k in (
            "tomtom",
            "live traffic",
            "current traffic",
            "current speed",
            "free flow speed",
        )):

            places = re.findall(
                r"\b(?:at|near|around|in)\s+"
                r"([a-z][a-z\s-]{1,40}?)(?=[,?.!]|$)",
                message,
                flags=re.IGNORECASE,
            )

            if not places:
                return {
                    "answer": (
                        "Please specify a Bhubaneswar place, "
                        "for example 'Check TomTom live traffic "
                        "near Rasulgarh'."
                    ),
                    "tool_results": {},
                    "mode": "rule_based",
                }

            results = {}

            try:
                for place in places[:3]:
                    place = place.strip()

                    results[place] = functions[
                        "get_tomtom_flow"
                    ](place)

                return {
                    "answer": (
                        "Retrieved TomTom traffic flow for the "
                        "requested places. Check each result's "
                        "source, live status and cache status. "
                        "A cached response is not a fresh "
                        "network observation."
                    ),
                    "tool_results": {
                        "tomtom_flow": results
                    },
                    "mode": "rule_based",
                }

            except Exception as exc:
                return {
                    "answer": (
                        "TomTom traffic retrieval failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "tool_results": {
                        "tomtom_flow": results
                    },
                    "mode": "rule_based",
                }

        # -----------------------------------------------------
        # WEATHER
        # -----------------------------------------------------

        if any(k in msg for k in (
            "weather",
            "rain",
            "rainfall",
            "wind",
            "temperature",
            "humidity",
        )):

            try:
                data = functions["get_weather"]()

                if data.get("status") == "unavailable":
                    answer = (
                        "The weather service is unavailable. "
                        "No current weather value can be reported."
                    )
                else:
                    answer = (
                        f"Open-Meteo conditions: "
                        f"{data.get('temperature_c')} °C, "
                        f"humidity "
                        f"{data.get('relative_humidity_pct')}%, "
                        f"rainfall {data.get('rain_mm')} mm, "
                        f"wind {data.get('wind_speed_kmh')} km/h. "
                        "These are external model-derived conditions, "
                        "not roadside sensor readings."
                    )

                return {
                    "answer": answer,
                    "tool_results": {"weather": data},
                    "mode": "rule_based",
                }

            except Exception as exc:
                return {
                    "answer": (
                        "Weather retrieval failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "tool_results": {},
                    "mode": "rule_based",
                }

        # -----------------------------------------------------
        # FORECASTS
        # -----------------------------------------------------

        if self._is_forecast_request(message):

            start = time.perf_counter()

            try:
                locations = self.traffic.list_locations()

                location = next(
                    (
                        item
                        for item in locations
                        if item.casefold() in msg
                    ),
                    None,
                )

                if not location:
                    return {
                        "answer": (
                            "Please specify a configured synthetic "
                            "dataset location for the forecast. "
                            "You can check the available locations "
                            "in the application's traffic dashboard."
                        ),
                        "tool_results": {},
                        "mode": "rule_based",
                    }

                horizon = (
                    60
                    if re.search(
                        r"\b60\b|60[- ]minute|hourly",
                        msg,
                    )
                    else 30
                )

                result = functions["get_forecast"](
                    location=location,
                    horizon_minutes=horizon,
                )

                print(
                    "[FORECAST DEBUG] Total request time: "
                    f"{time.perf_counter() - start:.2f}s",
                    flush=True,
                )

                return {
                    "answer": (
                        f"Retrieved the {horizon}-minute forecast "
                        f"for {location}. This uses the bundled "
                        "synthetic traffic history, not live "
                        "roadside traffic measurements."
                    ),
                    "tool_results": {
                        "forecast": result
                    },
                    "mode": "rule_based",
                }

            except Exception as exc:
                elapsed = time.perf_counter() - start

                print(
                    f"[FORECAST ERROR] Failed after {elapsed:.2f}s: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

                return {
                    "answer": (
                        "The traffic forecast could not be generated. "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "tool_results": {},
                    "mode": "rule_based",
                }

        # -----------------------------------------------------
        # APPLICATION-RECORDED INCIDENTS
        # -----------------------------------------------------

        if any(k in msg for k in (
            "incident",
            "accident",
            "closure",
            "roadwork",
        )):

            try:
                incidents = functions["list_incidents"]()

                return {
                    "answer": (
                        f"There are {len(incidents)} active incident "
                        "record(s) in this application session."
                    ),
                    "tool_results": {
                        "incidents": incidents
                    },
                    "mode": "rule_based",
                }

            except Exception as exc:
                return {
                    "answer": (
                        "Incident retrieval failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "tool_results": {},
                    "mode": "rule_based",
                }

        # -----------------------------------------------------
        # ML PREDICTIONS
        # -----------------------------------------------------

        if self._is_prediction_request(message):

            start = time.perf_counter()

            try:
                predictions = functions[
                    "get_traffic_predictions"
                ]()

                high = [
                    p for p in predictions
                    if p.get("predicted_congestion") == "High"
                ]

                medium = [
                    p for p in predictions
                    if p.get("predicted_congestion") == "Medium"
                ]

                low = [
                    p for p in predictions
                    if p.get("predicted_congestion") == "Low"
                ]

                print(
                    "[ML DEBUG] Total prediction request time: "
                    f"{time.perf_counter() - start:.2f}s",
                    flush=True,
                )

                return {
                    "answer": (
                        f"Retrieved {len(predictions)} location "
                        f"predictions.\n"
                        f"High congestion: {len(high)}\n"
                        f"Medium congestion: {len(medium)}\n"
                        f"Low congestion: {len(low)}\n\n"
                        "These are historical synthetic-data ML "
                        "predictions, not live traffic readings."
                    ),
                    "tool_results": {
                        "predictions": predictions
                    },
                    "mode": "rule_based",
                }

            except Exception as exc:
                print(
                    f"[ML ERROR] {type(exc).__name__}: {exc}",
                    flush=True,
                )

                return {
                    "answer": (
                        "Traffic predictions could not be generated: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "tool_results": {},
                    "mode": "rule_based",
                }

        # -----------------------------------------------------
        # DEFAULT RESPONSE
        # -----------------------------------------------------

        return {
            "answer": (
                "I can check live TomTom traffic, current weather, "
                "recorded incidents, historical-data ML predictions, "
                "30-minute and 60-minute forecasts, and routes "
                "in Bhubaneswar. Tell me what you want to investigate."
            ),
            "tool_results": {},
            "mode": "rule_based",
        }

    # =========================================================
    # OLLAMA AGENT
    # =========================================================

    def _ollama_chat(self, message: str) -> dict[str, Any]:

        functions = self._tool_functions()
        collected = {}

        system = """
You are the AI Traffic Copilot for the Bhubaneswar Traffic Bottleneck
Prediction and Smart Routing System.

You have access to backend tools. Use them for factual questions.

STRICT DATA RULES:
1. Never invent traffic speeds, travel times, weather values or incidents.
2. For live traffic questions, use get_tomtom_flow.
3. For incident, accident, closure or route-incident questions use get_live_incidents / get_closed_roads / check_route_incidents. Report the provider state exactly (live, cached, unavailable, not configured). Manual and simulated incidents are test data and must never be called live.
4. Never substitute historical synthetic ML predictions for live traffic.
5. Clearly distinguish fresh network data from cached data.
6. Explain that Open-Meteo provides model-derived weather, not roadside sensors.
7. Explain that OSM road geometry does not itself provide live traffic speeds.
8. For forecasts, use get_forecast and disclose the synthetic dataset source.
9. For route requests, use plan_route.
10. If a tool fails, report the failure rather than inventing an answer.
11. Do not call data tools for greetings or casual conversation.
12. Summarize only facts supported by tool outputs.
13. Explain conceptual questions directly and clearly.
14. Do not call ML prediction tools just because a question mentions congestion
    or bottlenecks. Use prediction tools only when the user explicitly asks
    for predictions or predicted locations.

Be concise, accurate and helpful.
"""

        messages = [
            {
                "role": "system",
                "content": system,
            },
            {
                "role": "user",
                "content": message,
            },
        ]

        for _ in range(5):

            response = httpx.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": messages,
                    "tools": self._ollama_tools(),
                    "stream": False,
                },
                timeout=180.0,
            )

            response.raise_for_status()

            result = response.json()

            assistant_message = result.get(
                "message",
                {},
            )

            messages.append(assistant_message)

            tool_calls = assistant_message.get(
                "tool_calls",
                [],
            )

            if not tool_calls:
                return {
                    "answer": assistant_message.get(
                        "content",
                        "",
                    ),
                    "tool_results": collected,
                    "mode": "ollama_tool_calling",
                    "model": self.ollama_model,
                }

            for call in tool_calls:

                function_data = call.get(
                    "function",
                    {},
                )

                name = function_data.get("name")

                arguments = function_data.get(
                    "arguments",
                    {},
                )

                if isinstance(arguments, str):
                    arguments = json.loads(arguments)

                if not isinstance(arguments, dict):
                    arguments = {}

                fn = functions.get(name)

                if fn is None:
                    output = {
                        "error": f"Unknown tool: {name}"
                    }

                else:
                    try:
                        output = fn(**arguments)

                    except Exception as exc:
                        output = {
                            "error": (
                                f"{type(exc).__name__}: {exc}"
                            )
                        }

                collected[name or "unknown"] = output

                messages.append({
                    "role": "tool",
                    "tool_name": name or "unknown",
                    "content": json.dumps(
                        output,
                        default=str,
                    ),
                })

        return {
            "answer": (
                "The Copilot reached its tool-call limit. "
                "Please try a more specific question."
            ),
            "tool_results": collected,
            "mode": "ollama_tool_calling",
            "model": self.ollama_model,
        }

    # =========================================================
    # PUBLIC CHAT METHOD
    # =========================================================

    def chat(self, message: str) -> dict[str, Any]:

        message = str(message).strip()

        if not message:
            return {
                "answer": "Please enter a message.",
                "tool_results": {},
                "mode": "validation",
            }

        # -----------------------------------------------------
        # GREETINGS
        # -----------------------------------------------------

        if re.fullmatch(
            r"\s*(hi|hello|hey|good morning|good afternoon|"
            r"good evening|thanks|thank you)[!. ]*\s*",
            message,
            re.IGNORECASE,
        ):
            return self._rule_based(message)

        # -----------------------------------------------------
        # ROUTES: BYPASS OLLAMA
        # -----------------------------------------------------

        # -----------------------------------------------------
        # INCIDENTS (incl. "incidents on my route" / "alternative route"): BYPASS OLLAMA
        # -----------------------------------------------------

        if self._is_incident_intent(message):

            print(
                "[COPILOT] Direct incident request.",
                flush=True,
            )

            return self._rule_based(message)

        route_request = self._extract_route_request(message)

        if route_request:

            print(
                f"[COPILOT] Direct route request detected: "
                f"{route_request}",
                flush=True,
            )

            result = self._rule_based(message)

            print(
                "[COPILOT] Direct route response completed.",
                flush=True,
            )

            return result

        # -----------------------------------------------------
        # TOMTOM INCIDENTS: BYPASS OLLAMA
        # -----------------------------------------------------

        if self._is_tomtom_incident_request(message):

            print(
                "[COPILOT] Direct TomTom incident request.",
                flush=True,
            )

            return self._rule_based(message)

        # -----------------------------------------------------
        # FORECASTS: BYPASS OLLAMA
        # -----------------------------------------------------

        if self._is_forecast_request(message):

            print(
                "[COPILOT] Direct forecast request detected.",
                flush=True,
            )

            start = time.perf_counter()

            result = self._rule_based(message)

            print(
                "[COPILOT] Forecast response completed in "
                f"{time.perf_counter() - start:.2f}s",
                flush=True,
            )

            return result

        # -----------------------------------------------------
        # ML PREDICTIONS: BYPASS OLLAMA
        # -----------------------------------------------------

        if self._is_prediction_request(message):

            print(
                "[COPILOT] Direct ML prediction request detected.",
                flush=True,
            )

            start = time.perf_counter()

            result = self._rule_based(message)

            print(
                "[COPILOT] ML prediction response completed in "
                f"{time.perf_counter() - start:.2f}s",
                flush=True,
            )

            return result

        # -----------------------------------------------------
        # PROVIDER SELECTION
        # -----------------------------------------------------

        provider = os.getenv(
            "COPILOT_PROVIDER",
            "ollama",
        ).lower()

        # -----------------------------------------------------
        # OLLAMA
        # -----------------------------------------------------

        if provider == "ollama":

            try:
                return self._ollama_chat(message)

            except Exception as exc:

                print(
                    f"[COPILOT] Ollama error: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

                try:
                    fallback = self._rule_based(message)

                    fallback["mode"] = (
                        "rule_based_after_ollama_error"
                    )

                    fallback["llm_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )

                    return fallback

                except Exception as fallback_exc:
                    return {
                        "answer": (
                            "The Copilot could not process this "
                            "request. "
                            f"{type(fallback_exc).__name__}: "
                            f"{fallback_exc}"
                        ),
                        "tool_results": {},
                        "mode": "error",
                    }

        # -----------------------------------------------------
        # OPTIONAL OPENAI PROVIDER
        # -----------------------------------------------------

        if provider == "openai":

            api_key = os.getenv("OPENAI_API_KEY")

            if api_key:

                try:
                    from openai import OpenAI

                    client = OpenAI(
                        api_key=api_key,
                        timeout=30.0,
                    )

                    response = client.responses.create(
                        model=os.getenv(
                            "OPENAI_MODEL",
                            "gpt-4.1-mini",
                        ),
                        input=message,
                    )

                    return {
                        "answer": response.output_text,
                        "tool_results": {},
                        "mode": "openai_text_only",
                    }

                except Exception as exc:

                    print(
                        f"[COPILOT] OpenAI error: {exc}",
                        flush=True,
                    )

                    try:
                        fallback = self._rule_based(message)

                        fallback["mode"] = (
                            "rule_based_after_openai_error"
                        )

                        fallback["llm_error"] = str(exc)

                        return fallback

                    except Exception as fallback_exc:
                        return {
                            "answer": (
                                "The Copilot could not process "
                                "this request. "
                                f"{type(fallback_exc).__name__}: "
                                f"{fallback_exc}"
                            ),
                            "tool_results": {},
                            "mode": "error",
                        }

            print(
                "[COPILOT] OPENAI_API_KEY is missing. "
                "Using rule-based fallback.",
                flush=True,
            )

        # -----------------------------------------------------
        # RULE-BASED FALLBACK
        # -----------------------------------------------------

        return self._rule_based(message)
