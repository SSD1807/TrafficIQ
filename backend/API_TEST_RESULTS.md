# FastAPI integration test results

> **Historical v2 report.** Written before the v3 final release. Statements here about TomTom mapping, test counts, dashboard launch, screenshots, or dependency/runtime versions are superseded by [`FEATURE_STATUS.md`](FEATURE_STATUS.md).

## Execution method

Two levels of API verification were run from the project root:

1. `TestClient` exercised every registered REST endpoint with valid and invalid request shapes. Raw responses are in `verification/api_results.json`; console output is in `verification/api_endpoint_execution.log`.
2. A real local Uvicorn process was started on `127.0.0.1:8010`. Requests were sent over HTTP to the running server. Startup and request logs are in `verification/uvicorn_startup.log` and `verification/uvicorn_live_http.log`.

## Endpoint coverage

| Endpoint | Valid request | Invalid / unavailable case | Observed result |
|---|---|---|---|
| `GET /` | Yes | N/A | 200 |
| `GET /health` | Yes | N/A | 200; classifier and forecast artifacts available |
| `GET /locations` | Yes | N/A | 200; locations identified as synthetic dataset |
| `GET /traffic/current` | Yes | N/A | 200; bundled historical synthetic state, not live |
| `GET /traffic/predictions` | Yes | N/A | 200; per-location predictions |
| `POST /traffic/predict/{location}` | Yes | Unknown location | 200 / 400 |
| `GET /forecasts/{location}` | 30 and 60 min | Unsupported 45 min | 200 / 400 |
| `GET /routing/nodes` | Yes | `limit=0` | 200 / 422; graph source `synthetic_fallback` |
| `POST /routes/calculate` | Dijkstra and A* | Incomplete endpoint pair | 200 / 422 |
| `GET /incidents` | Yes | N/A | 200 |
| `POST /incidents` | Road closure | Unknown edge | 201 / 400 |
| `DELETE /incidents/{id}` | Valid incident ID | Unknown ID | 200 / 404 |
| `DELETE /incidents` | Clear all | N/A | 200 |
| `GET /weather/current` | Request without fallback; explicit fallback | Live provider unavailable | 200 with service status `unavailable` or `simulated_fallback` |
| `GET /traffic/tomtom/flow` | Adapter endpoint | No API key | 200 with service status `disabled` |
| `GET /traffic/tomtom/incidents` | Adapter endpoint | Reversed bounding box | 200 disabled / 400 invalid bounds |
| `GET /routes/osrm` | Valid coordinate shape | External network unavailable | 502 due DNS/network error |
| `POST /copilot/chat` | Non-empty prompt | Empty prompt | 200 / 422 |

The `TestClient` run executed **31 endpoint cases**: 22 successful HTTP responses and 9 expected validation/provider-error responses. A successful HTTP 200 from the weather or TomTom endpoint does not imply that a provider returned live data; inspect the JSON `status`, `source`, `is_live` and `is_simulated` fields.

## Live local-server smoke test

The Uvicorn process started successfully. Local HTTP checks returned 200 for root, health, locations, current traffic, all predictions, routing node discovery, both forecast horizons, classification inference, Copilot rule fallback and route calculation.

Example local API results (bundled synthetic history):

| Check | Result |
|---|---|
| `POST /traffic/predict/Airport Road` | `Medium`; model label `Random Forest`; `is_live=false` |
| `GET /forecasts/Airport Road?horizon_minutes=30` | 428.69 vehicles, 68.51 km/h, `Medium`; target 2023-03-14 22:00 |
| `GET /forecasts/Airport Road?horizon_minutes=60` | 428.26 vehicles, 67.92 km/h, `Medium`; target 2023-03-14 22:30 |
| `POST /routes/calculate` | Route `1→3→8`, 1.65 km, 2.83 min on synthetic fallback graph |
| `POST /copilot/chat` | Rule-based response returned; OpenAI API was not enabled |

## External API limitations

- Open-Meteo was actually requested from the runtime. The request failed before HTTP response with DNS resolution error for `api.open-meteo.com`. Without fallback the adapter returned `status=unavailable`; with explicit fallback it returned `status=simulated_fallback`, `is_live=false`, `is_simulated=true`.
- TomTom live calls were not made because `TOMTOM_API_KEY` was not configured. Both endpoints returned `status=disabled`. Separate mocked adapter tests confirm that the request uses the `key` query parameter and current documented Flow Segment Data `unit=kmph`; the Incident Details path is v5. Official TomTom documentation lists India for both Traffic Flow and Incident Details, but a real Bhubaneswar request and exact road-segment coverage were not verified.
- OSRM was attempted with valid coordinates but failed with a DNS/network resolution error, surfaced as HTTP 502.
- OpenAI tool-calling was not invoked because neither the SDK nor `OPENAI_API_KEY` was available. Rule-based Copilot was executed successfully.

**Conclusion:** The local FastAPI application and its tested internal services work. External provider calls are not validated in this environment.
