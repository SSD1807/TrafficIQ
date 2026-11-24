# Technical audit and integration notes

> **Historical v2 report.** Written before the v3 final release. Statements here about TomTom mapping, test counts, dashboard launch, screenshots, or dependency/runtime versions are superseded by [`FEATURE_STATUS.md`](FEATURE_STATUS.md).

## Scope of source comparison

The accessible source artifacts at implementation time were:

1. The original `traffic_bottleneck(1).zip` archive.
2. The earlier `traffic_bottleneck_phase1_updated.zip` baseline created in the preceding project turn.
3. The Phase 1 report and metrics JSON supplied with that baseline.

The separately mentioned Gemini-modified ZIP was **not available in the conversation attachments or build workspace**. It could therefore not be independently diffed or merged. This release does not claim to have audited Gemini-only changes. If that archive is added later, it should be reviewed as a separate merge input rather than blindly overlaid.

The original archive contained the original scripts, generated CSV datasets, stored model artifacts, Streamlit dashboard, simulation code, visualizations, and Git metadata. The release excludes `.git`, bytecode and local runtime caches, but retains the source files, bundled datasets and useful legacy interfaces.

## Original system inventory

| Area | Original implementation | Audit finding |
|---|---|---|
| Data preparation | `phase1_preprocess.py` | Did not establish one clear target/inference contract for next-interval prediction. |
| Model training | Random Forest and XGBoost scripts | Same-interval traffic measurements could be used to infer labels derived from those measurements; random splitting did not model temporal generalization. |
| Dataset merge | `merge_and_retrain.py` | Root-relative defaults missed files under `dataset/`; Metro Interstate and Bhubaneswar features did not have equivalent semantics; missing speed values caused rows to be discarded. |
| Saved model | Joblib pickle files | Model and feature schema were not versioned as one self-describing inference contract. |
| Simulation | `simulation_engine.py`, `phase3_graph_simulate.py` | Randomness and hash-derived values were not consistently reproducible; feature engineering differed between training and inference. |
| Road graph | Manually specified NetworkX graph | It was an illustrative topology, not Bhubaneswar road geometry. |
| Route choice | NetworkX path routines | Dijkstra was not explicitly surfaced as a selectable method; A* was absent; road closures were not represented as unavailable edges. |
| Incidents | Local accident simulation | Expiry, multiple incidents and severity-aware route penalties were incomplete. |
| Dashboard | Streamlit and custom animation component | Useful legacy components existed but were not separated from new backend responsibilities. |

## Data and ML corrections

### Prediction contract

The classifier predicts the congestion class at the **next consecutive 30-minute interval for the same location**. A training sample is valid only when the following record for that location is exactly 30 minutes after the current observation.

The learned classifier sees:

- location as a categorical feature;
- calendar values for the target interval (time, weekday, weekend, rush-hour/night flags, cyclical encodings); and
- current-interval measurements as explicitly lagged vehicle-count, speed and weather features.

It does not receive the target interval's vehicle count, speed, weather or congestion label. The current congestion label is reserved for the independent persistence baseline.

### Dataset strategy

`dataset/bhubaneswar_synthetic_traffic.csv` is the primary source for the Bhubaneswar classifier and forecast experiments. It contains 35,000 generated rows across 10 location labels at half-hour intervals. The classifier's valid consecutive-interval set has 34,990 rows.

`dataset/Metro_Interstate_Traffic_Volume.csv` is retained for future separate experiments. It describes a different geography, time cadence and measurement domain. It is not concatenated into the Bhubaneswar model's training table. The previous merged file is regenerated as a Bhubaneswar-only compatibility dataset so legacy dashboard code does not silently consume cross-domain rows.

### Evaluation

The classifier uses a 70/15/15 chronological split over unique prediction timestamps. Every location associated with a given prediction timestamp remains in the same partition. Preprocessing (numeric imputation and categorical imputation/one-hot encoding) is inside fitted sklearn Pipelines. The model is selected on validation macro-F1 and then evaluated once on the test partition.

A persistence baseline, Random Forest and XGBoost are scored on the same 5,260-row test period. Test results and High-class false-positive/false-negative counts are in `outputs/phase1_metrics.json`; plots are in `outputs/`.

## Canonical architecture introduced

| Module | Responsibility |
|---|---|
| `ml_pipeline.py`, `phase1_preprocess.py`, `phase2_train.py` | Canonical target alignment, feature schema, temporal split, training, metrics and persistence comparison. |
| `forecasting/` | Separate regression and congestion-class forecasting for +30 and +60 minutes; separate optional SARIMA runner. |
| `routing/osm_network.py` | OSMnx drive-graph acquisition/cache with an explicitly labelled synthetic fallback. |
| `routing/route_optimizer.py` | Dijkstra, A*, alternative routes, distance/time cost and segment geometry. |
| `routing/incident_manager.py` | Multi-incident store with severity, directionality and expiry. |
| `simulation/traffic_simulator.py` | Seeded, deterministic, time-of-day and road-class congestion scenario generation. |
| `services/` | Traffic inference, Open-Meteo, TomTom and OSRM service adapters. |
| `backend/` | FastAPI endpoints and Pydantic request validation. |
| `copilot/` | OpenAI Responses function tools and a local rule-based fallback. |
| `dashboard.py` | New multi-section Streamlit dashboard. |
| `dashboard_legacy.py`, `animation_component.py`, `simulation_engine.py` | Retained legacy UI/visualization/simulation components. |

## External-data and live-system boundary

### OpenStreetMap / OSMnx

The code requests a driving graph for Bhubaneswar with `network_type="drive"`, simplifies the topology, keeps disconnected components, enriches edge speed/travel-time attributes where possible, and caches GraphML. OSMnx's drive network represents driving directionality and one-way street constraints. The graph provider falls back to a small illustrative synthetic graph if OSMnx or network access is unavailable. That fallback has approximate placeholder coordinates; it is not a map of actual roads.

The build environment used for this release did not have OSMnx installed. Therefore no real Bhubaneswar OSM graph was downloaded or bundled. The OSMnx acquisition path is implemented but requires installing dependencies and successful access to OSM/Overpass/Nominatim.

### Weather

The Open-Meteo adapter requests current model-derived conditions for Bhubaneswar coordinates, including temperature, rain/precipitation, cloud cover and wind. It has request timeouts, an in-memory cache and explicit unavailable/stale/simulated states. The adapter and cache behavior were tested with mocked HTTP responses. A real request was also attempted during final verification, but the runtime could not resolve `api.open-meteo.com`; the adapter returned `unavailable` without fallback and returned an explicitly labelled synthetic result only when fallback was opted into. Open-Meteo returns weather-model conditions, not roadside observations.

### TomTom

TomTom Flow Segment Data and Incident Details adapters are exposed through API endpoints. They require `TOMTOM_API_KEY`. No key was configured, so no live TomTom request was made. Mocked transport tests verify that the Flow Segment Data adapter sends the `key` query parameter and lowercase `kmph` unit, and that Incident Details uses the v5 path and bounding-box parameter. The current official coverage table lists India for both Traffic Flow and Traffic Incidents, but exact Bhubaneswar road-segment availability, subscription entitlement, live payload, quota and authentication cannot be proven without a real key/request. The official Incident Details page recommends considering TomTom Orbis APIs for new integrations. Returned flow/incident responses are not map-matched automatically onto OSM edges; therefore they do not affect route weights. Official provider references: [Flow Segment Data](https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data), [Incident Details](https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-incidents/incident-details), and [Market Coverage](https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/product-information/market-coverage).

### OSRM

An optional OSRM route comparison adapter and endpoint are included. A valid-coordinate OSRM request was attempted during final verification but failed DNS resolution; the endpoint returned HTTP 502. OSRM route duration is not represented as a live-traffic travel time.

### OpenAI Copilot

The Copilot exposes backend functions for traffic predictions, forecasts, weather, incidents and route planning. The OpenAI Responses API tool-calling path is implemented but was not invoked because no OpenAI API key was configured. The rule-based fallback was executed and has a natural-language route-query path. The LLM is instructed to use backend outputs and identify simulated/historical/external provenance; it is not a substitute for provider validation.

## Implemented release capabilities

| Capability | Status in source | Verification boundary |
|---|---|---|
| Leakage-aware 30-minute classifier | Complete | Trained/re-trained and held-out metrics reproduced. |
| Persistence/RF/XGBoost comparison | Complete | Same chronological test period; per-class metrics and confusion matrices saved. |
| 30/60-minute numeric forecasting | Complete | RF/XGBoost and persistence/moving-average baselines executed on the synthetic panel. |
| 30/60-minute class forecasting | Complete | Separate RF classifier trained/evaluated for each horizon. |
| SARIMA | Optional | Bounded rolling-origin, one-location pilot; a full panel run was not performed in the final verification. |
| LSTM | Not implemented in this release | Deliberately deferred; source is synthetic/short, TensorFlow was absent and no observed traffic series justifies this model yet. |
| OSMnx road acquisition | Implemented | Not exercised because OSMnx was absent; fallback routing was tested. |
| Dijkstra and A* | Complete | Executed and compared on offline fallback graph. |
| Incidents and rerouting | Complete for in-app/manual/simulated incidents | Severity, closure, reverse direction, expiry and rerouting tested. |
| Open-Meteo | Adapter complete | Mocked response/cache/fallback tests passed; live request not verified. |
| TomTom | Adapter/endpoints complete | Not verified against service; no key; not map-matched to route edges. |
| FastAPI | Complete | TestClient endpoints exercised without external dependencies. |
| Streamlit | Source complete | Not launched or visually verified; Streamlit packages were absent. Legacy dashboard retained. |
| OpenAI Copilot | Function-calling path and rule fallback implemented | Only the rule-based path was tested; no API key/model call was available. |

## Known limitations before a real-world pilot

1. Every included traffic observation and congestion label is synthetic. It does not establish real-world Bhubaneswar prediction or travel-time accuracy.
2. The forecasting metrics are model performance on a later synthetic time window, not field performance.
3. No real OSM geometry was downloaded during this build; OSMnx must be installed and the network must be reachable at runtime.
4. Open-Meteo and OSRM live calls were attempted but failed DNS resolution; TomTom live calls were not made without a key. TomTom flow/incident output is not yet map-matched to route edges.
5. Current traffic endpoint data is the latest row in the bundled synthetic history and is explicitly not current road traffic. A live weather override does not turn the synthetic traffic history into a real-time traffic feed.
6. Streamlit was not launched or visually checked in this environment. Its source compiles, and the API/service paths are tested separately.
7. The OpenAI tool-calling path requires a key and model access; the local rule-based Copilot works without one.
8. Incident storage is in-memory per process and is not shared/persistent across multiple workers or restarts.
9. SARIMA is only a bounded one-location pilot. LSTM was intentionally deferred.
10. The Gemini-modified ZIP needs to be supplied separately for a true three-way archive/source diff.


## Final integration verification (5 November 2026)

The final source tree was re-tested after integration corrections. `pytest -q` completed with **27 passed**; `python -m compileall -q .` passed. Phase 1 preprocessing/training, the 30/60-minute forecasting pipeline and the bounded one-location SARIMA pilot were executed again. A second Phase 1 training run produced identical SHA-256 values for the metrics JSON and model metadata.

A local Uvicorn server was started and exercised over HTTP. TestClient ran 31 endpoint cases, including valid and invalid request shapes. See `INTEGRATION_AUDIT.md`, `API_TEST_RESULTS.md`, `ROUTING_TEST_RESULTS.md` and the `verification/` directory.

Final environment blockers: `osmnx`, `streamlit`, `streamlit-folium` and `openai` are not installed; `TOMTOM_API_KEY` and `OPENAI_API_KEY` are not set; TomTom request construction was mock-tested but live access was not; Open-Meteo and OSRM host resolution failed. Docker is not installed. Therefore real OSM geometry, live TomTom traffic, live Open-Meteo data, OpenAI calls, the Streamlit UI and container deployment remain unverified.

The expanded implementation is not a fully verified live-traffic service. Traffic observations and model metrics remain synthetic. The latest dashboard corrections are recorded in `INTEGRATION_AUDIT.md`: Random Forest display name and distinct, accurate penalty descriptions for the new and retained legacy routing engines.


## 5 November deployment-readiness addendum

After the original integration audit, the dashboard was updated to load its graph before rendering the Overview source panel and to distinguish live, stale-cache, simulated and unavailable weather states. The Copilot UI now displays execution mode. `/health` now reports explicit provenance/configuration for traffic, road network, weather, TomTom and Copilot. The expanded test suite reports **28 passed**. Streamlit, Streamlit-Folium and OSMnx could not be installed because this runtime could not resolve the package index; therefore the UI and real OSM graph remain unexecuted. The latest deployment report is authoritative for current status.
