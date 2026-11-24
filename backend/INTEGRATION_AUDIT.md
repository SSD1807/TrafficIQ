# Final integration audit — Traffic Bottleneck Prediction & Smart Routing System

> **Historical v2 report.** Written before the v3 final release. Statements here about TomTom mapping, test counts, dashboard launch, screenshots, or dependency/runtime versions are superseded by [`FEATURE_STATUS.md`](FEATURE_STATUS.md).

**Verification date:** 5 November 2026 (runtime environment date)

## Overall conclusion

The expanded project source and local API can be executed, the Phase 1 classification and the 30/60-minute forecasting pipelines were rerun, routing/incident behavior was exercised on the offline fallback graph, and the automated suite passes. However, this is **not a fully verified live Bhubaneswar traffic system**: the real OSMnx graph, live weather, TomTom traffic, OpenAI calls and Streamlit UI could not be executed in this runtime because of missing dependencies/credentials or unavailable DNS/network access.

No live road traffic feed is attached to the synthetic traffic data. Static OpenStreetMap geometry, when available, is not live traffic.

## Feature status

| Feature | Status | Evidence and boundary |
|---|---|---|
| Phase 1 next-30-minute congestion classifier | **WORKING AND EXECUTED** | Preprocess and training scripts rerun; saved sklearn pipeline loaded by API. Synthetic data only. |
| Persistence / Random Forest / XGBoost comparison | **WORKING AND EXECUTED** | Same 5,260-row chronological holdout; all three metric sets and confusion matrices regenerated. RF remains selected by validation macro-F1. |
| Missing-value and unseen-location preprocessing | **WORKING AND EXECUTED** | Imputation and one-hot handling are inside pipeline; unit tests pass. |
| 30-minute numeric and class forecast | **WORKING AND EXECUTED** | Forecast script run; saved models called through forecast service and API. Synthetic historical origin. |
| 60-minute numeric and class forecast | **WORKING AND EXECUTED** | Same as above. |
| Forecast leakage controls | **WORKING AND EXECUTED** | Chronological split by target timestamp; target columns excluded from features; future-perturbation invariance test passes. |
| Persistence and moving-average forecast baselines | **WORKING AND EXECUTED** | Same horizon-specific holdout as learned forecast models. |
| SARIMA / SARIMAX | **PARTIALLY WORKING** | Executed 24-origin one-location pilot; not a full 10-location panel evaluation. |
| LSTM | **NOT IMPLEMENTED** | Intentionally deferred; not trained. |
| Real Bhubaneswar OSMnx road network | **IMPLEMENTED BUT NOT EXECUTED** | `osmnx` package absent; no OSM/Overpass download or real graph cache. |
| Offline fallback road graph | **SIMULATED** | 8 nodes / 24 directed edges, weakly connected; placeholder geometry only. |
| Dijkstra and A* | **WORKING AND EXECUTED** | Executed on three same-pair comparisons; matched optimal ETAs on fallback graph. |
| Alternative routes, distances and ETAs | **WORKING AND EXECUTED** | Route candidates and metrics returned on fallback graph. Not real-road estimates. |
| Accident penalties and rerouting | **WORKING AND EXECUTED** | Severity-1 accident caused 1→8 route to change from 1→3→8 to 1→5→8. |
| Road closure and rerouting | **WORKING AND EXECUTED** | Closure removes directed edge and changes selected route; verified by log and tests. |
| Time-of-day / rainfall traffic simulation | **SIMULATED** | Deterministic scenario simulator; not sensor observations. |
| Open-Meteo response parsing, cache and fallback logic | **WORKING AND EXECUTED** | Mock-response and fallback tests pass. |
| Open-Meteo live request | **PARTIALLY WORKING** | Real runtime request attempted but failed DNS resolution; service returns `unavailable` unless simulated fallback is explicitly opted in. |
| TomTom flow and incident adapter | **PARTIALLY WORKING** | Mocked adapter tests verify endpoint construction, query-key auth wiring, lowercase `kmph`, coordinates and bbox. No `TOMTOM_API_KEY`; live auth, subscription, returned data and local segment quality were not validated. Official docs list India for both flow and incident APIs (country-level coverage; exact segment availability is unverified). |
| TomTom flow-to-road-edge map matching | **NOT IMPLEMENTED** | The adapter returns provider data but does not map flow segments/incidents onto OSM edges for route-cost updates. |
| OSRM adapter | **PARTIALLY WORKING** | Valid-coordinate request attempted; DNS/network failure produced 502. No returned route to validate. |
| FastAPI backend | **WORKING AND EXECUTED** | Local Uvicorn server started; 31 TestClient endpoint cases and a live HTTP smoke test completed. |
| Dashboard business logic / common services | **PARTIALLY WORKING** | Source imports the same `backend.dependencies` service factories and saved models as API; UI itself could not start. |
| Streamlit dashboard UI | **IMPLEMENTED BUT NOT EXECUTED** | `streamlit` and `streamlit-folium` are absent; launch command returned `command not found`. No dashboard screenshots can be truthfully supplied. |
| Rule-based Traffic Copilot | **WORKING AND EXECUTED** | Traffic query and route-query fallback tests/API smoke test pass. |
| OpenAI function-calling Copilot | **IMPLEMENTED BUT NOT EXECUTED** | No `OPENAI_API_KEY` and `openai` package absent. |
| Docker / Compose deployment | **IMPLEMENTED BUT NOT EXECUTED** | Docker CLI is absent; container build/start not tested. |

## Reproduced Phase 1 result

The source contains 35,000 synthetic half-hour traffic rows for 10 location labels. Exact 30-minute consecutive pairing produced 34,990 supervised examples. The model split was chronological by prediction timestamp and separated every location at a timestamp into the same partition:

- Train: 24,490 rows
- Validation: 5,240 rows
- Test: 5,260 rows

| Model | Accuracy | Macro-F1 | High precision | High recall | High FP | High FN |
|---|---:|---:|---:|---:|---:|---:|
| Persistence | 91.24% | 78.71% | 51.05% | 51.05% | 93 | 93 |
| Random Forest | 96.06% | 88.03% | 64.71% | 75.26% | 78 | 47 |
| XGBoost | 95.10% | 86.78% | 52.07% | 92.63% | 162 | 14 |

Random Forest remains selected because its validation macro-F1 was higher. XGBoost catches more High cases on the test set but generates more false positives. The selected RF model has materially weaker High-class precision than Low/Medium precision, and the holdout contains only 190 High examples. Metrics describe the synthetic generator and are not real-world performance estimates.

Reproducibility check: a second `phase2_train.py` run produced identical SHA-256 values for `outputs/phase1_metrics.json` and `models/model_metadata.json`; hashes are in `verification/phase1_hash_before.txt` and `verification/phase1_hash_after.txt`.

## Forecast verification summary

The panel forecasting script was rerun for 30 and 60 minutes. For each horizon it used 27,990 or 27,980 train rows and 7,000 test rows, split chronologically by target timestamp. Features contain only origin-time and earlier observations plus known target-calendar fields. An automated perturbation test changes all measurements after a selected origin and confirms its feature row does not change while the future target does.

| Horizon | Target | RF MAE | RF RMSE | RF SMAPE |
|---|---|---:|---:|---:|
| 30 min | Vehicle count | 79.26 | 120.96 | 12.90% |
| 30 min | Average speed (km/h) | 3.36 | 4.36 | 8.51% |
| 60 min | Vehicle count | 76.19 | 115.61 | 12.54% |
| 60 min | Average speed (km/h) | 3.32 | 4.32 | 8.43% |

SARIMA pilot: Airport Road only, 24 one-step and 23 two-step origins per target. The SARIMA result is small-sample and separate from the panel regressors. No LSTM run was performed.

## Routing / incident verification

Only the synthetic fallback graph was available. Three same-pair algorithm comparisons gave identical optimal travel times for Dijkstra and A*. The `1→8` route changed from `1→3→8` (1.65 km, 2.83 min) to `1→5→8` (2.30 km, 3.94 min) under either a severity-1 accident or a road closure on `1→3`. The closure was absent from the selected route.

See `ROUTING_TEST_RESULTS.md` and `verification/routing_incident_execution.log`.

## API verification

- `pytest -q`: **27 passed**.
- `python -m compileall -q .`: passed.
- TestClient: 31 endpoint cases; 22 success responses plus 9 expected input/provider error responses.
- Real local Uvicorn: started and served health, data, prediction, forecast, routing, and Copilot requests.
- API endpoint payloads and logs: `API_TEST_RESULTS.md`, `verification/api_results.json`, `verification/api_endpoint_execution.log`, and `verification/uvicorn_live_http.log`.

## External service and UI verification blockers

- The environment cannot resolve `api.open-meteo.com`; live weather returned a DNS error. The fallback is explicitly simulated and separately labelled.
- `TOMTOM_API_KEY` is not set; TomTom auth, coverage, response fields and quota could not be tested.
- `osmnx`, `streamlit`, `streamlit-folium`, and `openai` are not installed in the runtime.
- `OPENAI_API_KEY` is not set.
- OSRM request failed DNS resolution.
- Docker CLI is not installed.
- As Streamlit is missing, the dashboard was not launched and no screenshots were generated. Code compilation succeeded, but a real UI integration check remains open.

## Corrected dashboard labels

- Prediction payloads now display `Random Forest` instead of the raw Python class name `RandomForestClassifier`.
- New routing dashboard describes its own actual cost factors: Low 1.00×, Medium 1.25×, High 1.75×, bounded rain factor, severity-dependent incident multiplier/delay, and edge removal for closures.
- Legacy simulation dashboard now identifies the shared Random Forest classifier and its own actual legacy values: Low 1×, Medium 4×, High 12×, Accident 500×. The old inaccurate 8×/30× descriptions were removed.

## Release contents

The complete source release retains the Phase 1 implementation, forecasting, routing, simulation, service adapters, FastAPI, Streamlit source, rule-based/LLM Copilot source, legacy dashboard/animation modules, tests, model artifacts, datasets, plots, configuration, reports and `verification/` logs. The separately mentioned Gemini ZIP was not in the accessible input files and could not be diffed; see `TECHNICAL_AUDIT.md`.

**Release status:** expanded local prototype delivered; not certified as a fully operational live-traffic service. Real OSMnx, external API credentials, the Streamlit UI and Docker runtime still require validation on a connected local machine.


## 5 November deployment-readiness addendum

The release was rechecked after the dashboard provenance and API health improvements. `pytest -q` now reports **28 passed** and `python -m compileall -q .` succeeds. A local Uvicorn process returned HTTP 200 for `/health`, `/weather/current` and `/traffic/tomtom/flow`; response payloads correctly distinguished synthetic/unavailable/disabled states.

Streamlit/Streamlit-Folium/OSMnx installation was attempted but the package index could not be resolved by DNS. `python -m streamlit run dashboard.py` failed with `No module named streamlit`. The OSM provider's forced load could not import OSMnx and selected its 8-node/24-edge synthetic fallback. Open-Meteo direct and API requests failed with DNS resolution errors. TomTom/OpenAI keys were not configured. See `DEPLOYMENT_READINESS_REPORT.md` and `verification/deployment_readiness/` for the new evidence, Windows instructions and exact status boundaries.
