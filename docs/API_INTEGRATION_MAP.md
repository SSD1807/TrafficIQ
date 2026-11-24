# Page → endpoint map (verified against the running API and `/openapi.json`, 17 paths)

| # | Page | Endpoints used | Notes |
|---|------|----------------|-------|
| – | Topbar status | `GET /health` | status badge is derived from the response, not hard-coded |
| 1 | Executive Overview | `/traffic/current`, `/traffic/predictions`, `/incidents`, `/health` | KPIs, speed bars, risk ranking, prediction map, data-source status |
| 2 | Traffic Map | `/traffic/current`, `/traffic/predictions`, `/incidents`, `/routing/nodes?limit=2000`, `/traffic/tomtom/flow` | incident edges drawn from graph node coordinates; TomTom status shown honestly |
| 3 | Traffic Prediction | `/locations`, `/traffic/current`, `POST /traffic/predict/{location}` | what-if overrides + optional Open-Meteo input |
| 4 | Forecasting Analytics | `/locations`, `/traffic/current`, `/forecasts/{location}?horizon_minutes=30\|60&model=random_forest\|xgboost` | 30 and 60 min, plus all-location comparison |
| 5 | Smart Route Planner | `/routing/nodes`, `POST /routes/calculate`, `/health` | node / map-click coordinates / place-name modes, Dijkstra & A*, 1–5 alternatives |
| 6 | Incident Management | `GET/POST/DELETE /incidents`, `DELETE /incidents/{id}`, `POST /incidents/refresh`, `GET /traffic/tomtom/incidents/status`, `POST /routes/incident-impact`, `/routing/nodes`, `POST /routes/calculate` | edges chosen from a calculated route's real segments; reroute shown after each change |
| 7 | Weather Intelligence | `GET /weather/current[?allow_simulated_fallback&force_refresh]` | `unavailable` shown as unavailable; simulated fallback only on explicit request |
| 8 | AI Traffic Copilot | `POST /copilot/chat`, `/health` | shows `mode` and the raw `tool_results` |
| 9 | Model Evaluation | `GET /models/evaluation` | **added endpoint**, see below |

## The one backend addition: `GET /models/evaluation`
No existing endpoint exposed evaluation metrics. This read-only endpoint returns the content of the **existing, saved**
`outputs/phase1_metrics.json`, `outputs/forecasting/forecast_metrics.json` and `models/model_metadata.json`.
It loads no model and recomputes nothing. Returns `503` if none of the files exist. Covered by tests.

## Schema facts the frontend relies on (all verified)
* Routing nodes use **`latitude` / `longitude`** (not `lat`/`lon`). A test asserts `lat`/`lon` are absent.
* `POST /routes/calculate`: **exactly one** endpoint pair (`origin_node`+`destination_node`, or four coordinates, or `origin_name`+`destination_name`); zero or several → `422`. The UI builds one pair per request.
* Route geometry: `routes[].polyline_latlon` (`[lat, lon][]`) is drawn; node coordinates are only a fallback.
* Incidents are **directed graph edges** (`edge_u`, `edge_v`) with 0–1 `severity`, `kind`, `source`, `expires_at`; there is no lat/lng, title or status. Unknown edge → `400`; deleting twice → `404`.
* Weather `status`: `ok | stale_cache | simulated_fallback | unavailable`; no forecast/visibility fields exist.
* Congestion classes are `Low | Medium | High` (no "severe").
* Not exposed by the backend → not shown: hourly/daily history, confidence intervals, weather forecast, feature importance, training curves, LSTM metrics.


## Live incidents (TomTom Incident Details) - added
`GET /incidents` (feed + counts + provider status), `POST /incidents/refresh`, `GET /traffic/tomtom/incidents[?refresh=true]`,
`GET /traffic/tomtom/incidents/status`, `POST /routes/incident-impact`; `POST /routes/calculate` additionally returns
`incident_summary`. Manual/simulated incidents keep `POST /incidents` / `DELETE /incidents[/{id}]`; TomTom ones are
provider-managed (`DELETE` -> 409). See `docs/LIVE_INCIDENTS.md`.
