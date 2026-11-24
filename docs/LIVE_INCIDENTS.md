# Live incident detection & automatic rerouting (TomTom Incident Details)

This feature adds **real, provider-reported traffic incidents** to the existing app. It sits next to - and is
independent of - the TomTom *Flow* integration, the manual incident form and the routing engine.

## What is live, what is not (read this before presenting)

| Data | What it is |
|---|---|
| Traffic dataset, ML predictions, 30/60-min forecasts | **Historical synthetic dataset** and model outputs on it. Not live. Unchanged by this feature. |
| TomTom flow (speed on matched road segments) | Live provider measurement *when a key is configured*; separate integration. |
| **TomTom incidents (this feature)** | **Live provider reports** from the Incident Details API v5 for the configured bounding box. |
| Manual / simulated incidents | User-created **test data**. Never labelled live. |
| Road geometry | OpenStreetMap (OSMnx graph, `backend/cache/bhubaneswar_drive.graphml`). |

An empty TomTom feed is a **valid** result (status `live_empty`). Without a key the status is `disabled_no_key` and
the feed contains **no** TomTom incidents; the app never fabricates any.

## Data flow

```
TomTom Incident Details v5  --(backend only, key in backend/.env)-->  services/tomtom_incidents.py
        parse + validate + classify errors, cache last good data, back-off, min-interval
                    |
                    v
services/incident_sync.py : match each incident geometry to DIRECTED OSM edges (routing/incident_matching.py)
                    |
                    v
routing/incident_manager.py : provider records (original data + match)  +  edge records priced by routing
                    |                                   (dedupe by provider id, update, expire, never erase on failure)
                    v
routing/route_optimizer.py (existing Dijkstra / A*, congestion, rainfall, flow costs - unchanged) -> alternative routes
                    |
                    v
FastAPI endpoints  ->  Incident Management page, Copilot tools
```

## Provider status values (`state`)

| state | meaning |
|---|---|
| `live` | last request succeeded recently and TomTom reported >= 1 incident |
| `live_empty` | last request succeeded recently; TomTom reported no incidents |
| `cached` | showing the last successful data; the latest request failed or no recent request. `stale: true` once older than `TOMTOM_INCIDENTS_STALE_AFTER` - stale incidents stay visible but are no longer applied to routing |
| `disabled_no_key` | `TOMTOM_API_KEY` is empty |
| `disabled` | `TOMTOM_INCIDENTS_ENABLED=false` |
| `unavailable` | request failed and there is no earlier data (auth, rate limit, timeout, network, 5xx, invalid response) |
| `idle` | enabled and configured but nothing requested yet |

Errors handled: HTTP 400, 401/403 (bad key), 429 (honours `Retry-After`, backs off), 5xx, timeouts, network errors,
non-JSON or schema-invalid responses. A failed request **never** removes previously fetched incidents. The API key is
never logged, returned by any endpoint, or sent to the browser.

## Road matching (how an incident becomes a road-edge effect)

1. Sample the incident geometry (GeoJSON Point/LineString, `[lon, lat]`) about every 10 m.
2. For each sample, find nearby directed edges (grid index); keep those within 30 m whose bearing agrees (+-45 deg) with
   the incident direction.
3. If two *different* roads fit almost equally (< 8 m apart) the sample is ambiguous. `roadNumbers` vs OSM `ref` can
   break the tie. Otherwise the incident is **not applied** (reason `ambiguous_parallel_roads`).
4. Matched only if enough of the geometry is covered and confidence is high enough. Otherwise **unmatched** with an
   explicit reason (`no_nearby_road`, `direction_mismatch`, `ambiguous_parallel_roads`, `low_confidence`,
   `outside_road_graph`, `road_graph_not_openstreetmap`, `invalid_geometry`). Unmatched incidents are shown on the
   map/feed as "provider incidents" and never touch a road edge.

Measured on the bundled real Bhubaneswar graph (mocked incident geometry derived from real edge shapes, 600 random
edges >= 60 m): LineStrings 3 m off the road matched the correct directed edge 90 % of the time (84 % at 8 m offset),
**0 % matched a wrong edge**, the rest were declined as ambiguous. Point incidents: 60 % matched, 0 % wrong, 40 %
declined (a point has no direction or extent, so parallel carriageways are ambiguous). These figures come from
`tests/test_incident_real_osm.py` logic and say nothing about TomTom's real incident geometry.

**Assumption to verify:** TomTom's documentation does not state that LineString vertices follow the direction of
travel. The matcher assumes they do. If your live data shows otherwise, set
`TOMTOM_INCIDENTS_DIRECTION_AWARE=false`.

## How incidents change routing

Only incidents that are **matched, confirmed (`probabilityOfOccurrence` certain/probable or absent) and currently
present** are applied. Others stay listed with the reason.

| TomTom category (`iconCategory`) | internal kind | routing effect |
|---|---|---|
| 8 Road closed | `road_closure` | directed edge removed from the routing view |
| 1 Accident | `accident` | cost factor and delay scale with severity |
| 9 Road works, 7 Lane closed, 14 Broken-down vehicle | `road_work` / `obstruction` | milder factor/delay |
| 2-5, 10, 11, 0 (fog, ice, rain, wind, flooding, dangerous, unknown) | `hazard` | mild factor/delay |
| 6 Jam | `traffic_jam` | delay by severity, **skipped on edges that already have measured TomTom flow** (no double counting) |

Severity comes from `magnitudeOfDelay` (1 minor 0.3, 2 moderate 0.6, 3 major 0.9; closures 1.0). If TomTom gives none, a
per-kind default is used and the UI says so. Each incident id is applied once per edge however often it is polled.
Rainfall, congestion model and measured-flow costs are untouched.

## API

| endpoint | purpose |
|---|---|
| `GET /incidents` | feed (all sources), counts, provider status; legacy `incidents` list kept |
| `POST /incidents/refresh` | fetch TomTom now (server rate-limited), match, reconcile; never raises for provider errors |
| `GET /traffic/tomtom/incidents[?refresh=true]` | TomTom incidents with match result |
| `GET /traffic/tomtom/incidents/status` | provider status, no external call |
| `POST /incidents`, `DELETE /incidents/{id}`, `DELETE /incidents[?source=]` | manual/simulated test incidents (TomTom ones are provider-managed: delete -> 409) |
| `POST /routes/calculate` | now also returns `incident_summary` |
| `POST /routes/incident-impact` | route without live incidents vs with all incidents (Dijkstra or A*), delta, incidents on each route |

## Configuration (`backend/.env`)

| variable | default | notes |
|---|---|---|
| `TOMTOM_API_KEY` | empty | backend only |
| `TOMTOM_INCIDENTS_ENABLED` | `true` | |
| `TOMTOM_INCIDENTS_POLL_INTERVAL` | `60` | seconds, minimum 30 |
| `TOMTOM_INCIDENTS_BACKGROUND_POLL` | `true` | poll while the backend runs (only if a key exists) |
| `TOMTOM_INCIDENTS_BBOX` | `85.74,20.22,85.92,20.37` | minLon,minLat,maxLon,maxLat; max 10,000 km2 |
| `TOMTOM_INCIDENTS_STALE_AFTER` | `900` | seconds before cached data stops affecting routing |
| `TOMTOM_INCIDENTS_TIMEOUT` | `8` | seconds |
| `TOMTOM_INCIDENTS_MIN_REFRESH_SECONDS` | `10` | server-side throttle between requests |
| `TOMTOM_INCIDENTS_MATCH_MAX_DISTANCE_M` / `_MIN_CONFIDENCE` / `TOMTOM_INCIDENTS_DIRECTION_AWARE` | `30` / `0.55` / `true` | matching |
| `frontend/.env` `VITE_INCIDENT_POLL_SECONDS` | `60` | optional, not secret |

## Limitations

* Only verified with **mocked** TomTom responses unless you run the opt-in live test with your own key.
* Uses the v5 Incident Details endpoint (the one in this project's TomTom integration). TomTom also documents a newer
  Orbis-based incident API; migration is not done.
* In-memory registry: incidents are re-fetched after a backend restart (manual/simulated ones are lost, as before).
* Matching is a heuristic; point incidents often cannot be placed confidently. Closures apply to the matched directed
  edge only; if TomTom reports a two-way closure as one LineString, only that direction is closed.
* A TomTom incident applies to whole OSM edges (the unit of routing), even if the incident covers part of a long edge.
* Free-tier TomTom quotas apply: do not lower the poll interval.
