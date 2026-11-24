"""Shared helpers for the TomTom incident tests. Everything here is mocked: no network, no API key."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import networkx as nx

from routing.incident_manager import IncidentManager
from routing.osm_network import haversine_m
from routing.route_optimizer import RouteOptimizer
from services.incident_sync import IncidentSyncService
from services.tomtom_incidents import DEFAULT_BBOX, IncidentConfig, TomTomIncidentService

LAT0, LON0, STEP = 20.2961, 85.8245, 0.004      # ~410 m between columns; row 1 is ~440 m north of row 0
M_LAT = 1 / 110_540.0                            # degrees of latitude per metre
node = lambda r, c: 1000 + r * 10 + c            # noqa: E731


def corridor_graph(*, two_way: bool = True) -> nx.MultiDiGraph:
    """2 rows x 4 columns. Row 0 = 'Corridor Road' (the fastest path); row 1 = 'Detour Road'; columns are cross streets."""
    g = nx.MultiDiGraph(source="test_grid")
    for r in range(2):
        for c in range(4):
            g.add_node(node(r, c), x=LON0 + c * STEP, y=LAT0 + r * STEP)

    def link(a, b, name, osmid, speed):
        d = haversine_m(g.nodes[a]["y"], g.nodes[a]["x"], g.nodes[b]["y"], g.nodes[b]["x"])
        pairs = ((a, b), (b, a)) if two_way else ((a, b),)
        for u, v in pairs:
            g.add_edge(u, v, 0, length=d, speed_kph=speed, highway="primary", oneway=not two_way, osmid=osmid, name=name)

    for c in range(3):
        link(node(0, c), node(0, c + 1), "Corridor Road", 1, 50.0)
        link(node(1, c), node(1, c + 1), "Detour Road", 2, 40.0)
    for c in range(4):
        link(node(0, c), node(1, c), f"Cross Street {c}", 10 + c, 40.0)
    return g


def parallel_roads_graph(gap_m: float = 10.0) -> nx.MultiDiGraph:
    """Two separate eastbound roads `gap_m` apart (e.g. a main road and a service road) - the classic ambiguity."""
    g = nx.MultiDiGraph(source="test_grid")
    for i, (name, osmid, dy) in enumerate((("Main Road", 1, 0.0), ("Service Road", 2, gap_m * M_LAT))):
        a, b = 2000 + i * 10, 2001 + i * 10
        g.add_node(a, x=LON0, y=LAT0 + dy)
        g.add_node(b, x=LON0 + STEP, y=LAT0 + dy)
        d = haversine_m(g.nodes[a]["y"], g.nodes[a]["x"], g.nodes[b]["y"], g.nodes[b]["x"])
        g.add_edge(a, b, 0, length=d, speed_kph=40.0, highway="primary", osmid=osmid, name=name,
                   ref="NH16" if osmid == 1 else None)
        g.add_edge(b, a, 0, length=d, speed_kph=40.0, highway="primary", osmid=osmid, name=name,
                   ref="NH16" if osmid == 1 else None)
        if g.edges[a, b, 0].get("ref") is None:
            del g.edges[a, b, 0]["ref"], g.edges[b, a, 0]["ref"]
    return g


def row0_line(c0: float, c1: float, north_m: float = 4.0, n: int = 6) -> list[list[float]]:
    """GeoJSON [lon, lat] vertices running along Corridor Road from column c0 to c1 (fractions of a column)."""
    return [[LON0 + (c0 + (c1 - c0) * i / (n - 1)) * STEP, LAT0 + north_m * M_LAT] for i in range(n)]


def incident(pid="inc-1", icon=8, magnitude=4, coords=None, *, geometry_type="LineString", start="2026-11-20T04:00:00Z",
             end="2026-11-20T18:00:00Z", description="Closed", probability="certain", **extra) -> dict:
    """One raw incident in the documented TomTom v5 response shape."""
    coords = coords if coords is not None else row0_line(1.0, 2.0)
    props = {"id": pid, "iconCategory": icon, "magnitudeOfDelay": magnitude, "startTime": start, "endTime": end,
             "from": "Corridor Road", "to": "Cross Street 2", "length": 410.0, "delay": 0, "roadNumbers": [],
             "timeValidity": "present", "probabilityOfOccurrence": probability,
             "events": [{"code": 401, "description": description, "iconCategory": icon}], **extra}
    return {"type": "Feature", "properties": props, "geometry": {"type": geometry_type, "coordinates": coords}}


def payload(*incidents) -> dict:
    return {"incidents": list(incidents)}


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeTomTom:
    """httpx MockTransport handler whose next response (or exception) is scripted per call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item, headers={"TrafficModelID": "1111"})


def make_service(fake: FakeTomTom, *, key="test-key", clock: Clock | None = None, **config) -> TomTomIncidentService:
    cfg = IncidentConfig(**{"bbox": DEFAULT_BBOX, **config})
    client = httpx.Client(transport=httpx.MockTransport(fake))
    return TomTomIncidentService(api_key=key, client=client, config=cfg, clock=clock or Clock())


def make_sync(fake: FakeTomTom, graph=None, *, key="test-key", clock: Clock | None = None, manager=None, **config):
    graph = graph if graph is not None else corridor_graph()
    clock = clock or Clock()
    service = make_service(fake, key=key, clock=clock, **config)
    manager = manager or IncidentManager()
    sync = IncidentSyncService(service, manager, lambda: (graph, "test_grid", True))
    return sync, service, manager, graph, clock


def optimizer_for(graph, manager) -> RouteOptimizer:
    return RouteOptimizer(graph, "test_grid", manager)


def patch_manager_clock(monkeypatch, clock: Clock) -> None:
    import routing.incident_manager as im
    monkeypatch.setattr(im, "_utcnow", lambda: datetime.fromtimestamp(clock.t, tz=timezone.utc))
