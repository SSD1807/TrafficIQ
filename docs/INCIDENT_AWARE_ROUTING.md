# Incident-aware routing, location search and road names

One backend engine serves **both** the Smart Route Planner and Incident Management
(`backend/routing/engine.py`, rules in `backend/routing/impact_rules.py`). The React pages contain no incident rules.

## Data flow
1. **Road network** - `OSMNetworkProvider.require_graph()` loads the cached real OSM GraphML (OSMnx; a NetworkX reader is the
   fallback when OSMnx cannot be imported - still real OSM data). If it is missing the API answers **HTTP 503** with `reason` and
   `hint`; the synthetic 8-node graph is never substituted. `GET /network/status`, `POST /network/download`, `GET /network/roads`.
2. **Location search** - `routing/geocoding.py`: road/place names of the loaded graph first, then Nominatim restricted to India and
   to the graph's bounding box (free text + structured query, transliteration variants, similarity check). Errors are typed:
   `not_found` (404), `geocoder_unavailable` (503, "not proof the place does not exist"), `outside_network`, `no_road_nearby`.
   The point is then snapped to the nearest node of the loaded graph (max `MAX_NODE_SNAP_DISTANCE_M`). `GET /locations/search?q=`.
3. **Labels** - `routing/labels.py`: node name -> junction of OSM road names -> "Near <nearest named OSM road within 150 m>" ->
   Nominatim reverse geocode (cached, only if reachable) -> "Unnamed road junction (lat, lon)". Node IDs stay the routing key and
   are only a last resort in the UI. Names are never invented.
4. **Incidents** - TomTom feed -> `incident_matching.match_incident` (geometry + direction + road numbers + provider from/to road
   names; ambiguous parallel roads stay unmatched and list the competing roads) -> `incident_manager` edge records.
5. **Impact** - `ImpactRules` (defaults overridable with `INCIDENT_IMPACT_RULES=<json file>`):

| Case | Routing effect |
|---|---|
| TomTom "Road closed", certain/probable, active, matched | `block`: the directed OSM edge is removed |
| Accident (major >= 0.8, moderate >= 0.5, else minor) | `penalty`, road stays passable |
| Road works / lane closed / obstruction / hazard / jam | `penalty` (lane counts are not provided by TomTom and never assumed) |
| Unconfirmed closure, unmatched, stale incidents | shown, **not applied** |

   Penalty per edge: provider delay (seconds, > 0) shared over matched edges by length - factor 1.0; otherwise the rule default
   `time * (1 + factor_per_severity * severity) + delay_base + delay_per_severity * severity` (labelled `rule_default`).
   Display score 0-100 = `100 * kind_weight * severity + length points + delay points` (ranking only).
6. **Plan** - `POST /routes/plan`: normal route (no incidents) -> incidents on it -> priced graph -> Dijkstra/A* + k alternatives ->
   decision. **Reroute** when the normal route is blocked by a confirmed closure, or the best priced alternative saves at least
   `reroute_min_saving_s` (120 s) **and** `reroute_min_saving_pct` (5 %). Otherwise the route is kept with warnings. Incidents off the
   route never change it. The response carries original / recommended / alternatives, distance and time deltas, incidents avoided,
   per-incident analysis and the data-source status. A route is only called incident-free if live incident data supports it.

All times are model estimates from OSM road attributes plus provider-reported incident delays; they are not live per-road speeds.
