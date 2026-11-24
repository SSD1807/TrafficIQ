# Traffic Bottleneck Prediction & Smart Routing System

> **v3 final (2026-11-10):** redesigned multi-section dashboard, OSM map/routing, pinned & retrained models (scikit-learn 1.9.1), TomTom-to-edge matching (mock-verified only). **Read [`FEATURE_STATUS.md`](FEATURE_STATUS.md) first** for what is verified and what is not, and [`DASHBOARD_V3_GUIDE.md`](DASHBOARD_V3_GUIDE.md) for usage. Screenshots: [`screenshots/`](screenshots/README.md).

A modular final-year project prototype for next-interval congestion classification, 30/60-minute traffic forecasting, OpenStreetMap-based road routing, external weather/traffic adapters, incident-aware route costs, and a tool-grounded Traffic Copilot.

> **Important data-provenance rule:** The included Bhubaneswar traffic CSV is synthetic. Its predictions and evaluation metrics are research/demo outputs, not verified real-world traffic accuracy. OpenStreetMap supplies road geometry and road attributes, not live traffic speeds. Weather and TomTom data are external only when the relevant network request succeeds and the service reports an external source.

## Current capability status

| Area | Implemented | Validation in this environment |
|---|---|---|
| Leakage-aware 30-minute congestion classification | Yes | Re-trained and evaluated |
| Persistence baseline comparison | Yes | Same chronological holdout |
| Random Forest / XGBoost metrics and High-class FP/FN | Yes | Executed |
| 30/60-minute volume and speed forecasting | Yes | RF/XGBoost/baselines executed |
| 30/60-minute congestion class forecasting | Yes | RF classifier executed |
| SARIMA | Optional per-location rolling-origin experiment | 24-origin Airport Road pilot; full panel not run |
| LSTM | Not trained | Deliberately skipped; see `FORECASTING_REPORT.md` |
| OSMnx road graph download/cache | Yes, optional dependency | Forced provider load attempted; OSMnx absent, synthetic fallback selected |
| Dijkstra and A* routing / alternative routes | Yes | Executed on synthetic fallback graph |
| Incident penalties, closure and expiry | Yes | Automated tests passed |
| Open-Meteo adapter/cache/weather provenance | Yes | Mocked response/cache tested; direct and API live requests attempted, DNS resolution failed |
| TomTom flow and incident adapters | Yes, API-key optional | Mocked request/auth construction tested; live calls disabled because no key |
| OSRM comparison adapter | Yes, optional external request | Attempted; DNS resolution failed |
| FastAPI REST backend | Yes | TestClient smoke tests passed |
| Streamlit dashboard | Yes; legacy dashboard preserved | Launch attempted; `No module named streamlit`; UI screenshots unavailable |
| OpenAI function-calling Copilot + rule fallback | Yes | Rule-based fallback executed; LLM not called (SDK/key absent) |

The Gemini-modified ZIP referenced in the execution request was not present among the available conversation files at build time. The accessible comparison sources were the original `traffic_bottleneck(1).zip` and the earlier `traffic_bottleneck_phase1_updated.zip`; see `TECHNICAL_AUDIT.md` for exact scope.

## Project structure

```text
traffic_bottleneck/
├── backend/
│   ├── main.py                 # FastAPI routes
│   ├── dependencies.py         # service construction / DI
│   └── schemas/api.py           # Pydantic contracts
├── copilot/
│   └── agent.py                # OpenAI function calling + rule fallback
├── forecasting/
│   ├── forecast_pipeline.py    # separate volume/speed/class forecasting
│   ├── forecast_service.py     # saved-model inference
│   └── sarima_runner.py        # optional per-location SARIMA experiment
├── routing/
│   ├── osm_network.py          # OSMnx graph download/cache + synthetic fallback
│   ├── route_optimizer.py      # Dijkstra, A*, alternatives, weighted routes
│   └── incident_manager.py    # incident severity, directionality, TTL
├── services/
│   ├── traffic_service.py
│   ├── weather_service.py
│   ├── tomtom_service.py
│   └── osrm_service.py
├── simulation/
│   └── traffic_simulator.py
├── tests/
├── dataset/
├── models/
├── outputs/
├── dashboard.py                # new integrated Streamlit dashboard
├── dashboard_legacy.py        # preserved original Streamlit dashboard
├── animation_component.py     # preserved original animation component
├── simulation_engine.py       # preserved legacy simulation engine
├── ml_pipeline.py             # Phase 1 target/features/split/model factory
├── phase1_preprocess.py
├── phase2_train.py
├── generate_bhubaneswar_traffic.py
├── PHASE1_ML_REPORT.md
├── FORECASTING_REPORT.md
└── TECHNICAL_AUDIT.md
```

## 1. Environment setup

Python 3.12 (tested) or 3.14 (all pinned packages have Windows wheels; not executed here), 64-bit. From the project root:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Configure optional services:

```bash
cp .env.example .env
# Add OPENAI_API_KEY and/or TOMTOM_API_KEY only if you have them.
```

No API key is required to use the synthetic data, fallback graph, local ML model, FastAPI API, or rule-based Copilot.

## 2. Rebuild Phase 1 classification artifacts

```bash
python phase1_preprocess.py
python phase2_train.py
pytest -q
```

The classifier's target is the observed congestion label at the next consecutive 30-minute interval for the same location. It uses the current interval's lagged measurements plus known calendar context. The current congestion label is used only by the persistence baseline, not by the learned classifier.

Key outputs:

- `models/best_model.pkl` — full fitted preprocessing + classifier Pipeline.
- `models/feature_cols.pkl` — canonical feature schema.
- `models/model_metadata.json` — target, schema and training provenance.
- `outputs/phase1_metrics.json` — RF, XGBoost and persistence validation/test results.
- `outputs/confusion_matrix_*.png` — individual confusion matrices.
- `outputs/model_comparison.png` — comparable test scores.

## 3. Train separate 30/60-minute forecasts

```bash
python -m forecasting.forecast_pipeline
```

Forecast targets are vehicle count and average speed at +30 and +60 minutes. Baselines are persistence and a 3-interval moving average. Random Forest and XGBoost are evaluated with a chronological holdout. A separate Random Forest classifier predicts the future congestion class for each horizon.

Optional SARIMA experiment:

```bash
python -m forecasting.sarima_runner --max-locations 1 --max-test-points 24
# Increase max-locations/max-test-points only when runtime permits.
```

SARIMA uses a separate per-location rolling-origin experiment: it predicts one and two intervals ahead at each holdout origin, then appends the observed value without refitting. Its output is kept separate from the shared panel regressor scores. Limit the experiment with `--max-test-points` and `--max-locations`; a full run can be computationally expensive. LSTM is not trained in this build; the justification is documented in `FORECASTING_REPORT.md`.

## 4. Start the FastAPI backend

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation.

Core endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | API/model/provider status |
| GET | `/locations` | Synthetic dataset location names |
| GET | `/traffic/current` | Latest row in bundled synthetic dataset (explicitly not live) |
| GET | `/traffic/predictions` | 30-minute classifier outputs for configured locations |
| POST | `/traffic/predict/{location}` | 30-minute prediction; optional Open-Meteo weather inputs |
| GET | `/forecasts/{location}?horizon_minutes=30` | 30-minute volume/speed/class forecast |
| GET | `/forecasts/{location}?horizon_minutes=60` | 60-minute volume/speed/class forecast |
| GET | `/routing/nodes` | Nodes in active graph |
| POST | `/routes/calculate` | Dijkstra/A*, alternatives, congestion/incident penalties |
| GET | `/routes/osrm` | Optional OSRM road-route comparison; not live traffic |
| GET/POST/DELETE | `/incidents` | List/create/clear incidents |
| DELETE | `/incidents/{id}` | Remove an incident |
| GET | `/weather/current` | Open-Meteo current model conditions; fallback is opt-in |
| GET | `/traffic/tomtom/flow` | TomTom flow data at a coordinate (requires key) |
| GET | `/traffic/tomtom/incidents` | TomTom incidents within a bounding box (requires key) |
| POST | `/copilot/chat` | AI tool-calling or rule-based assistant |

The `POST /routes/calculate` request accepts exactly one endpoint pair: coordinate pairs, geocodable place names, or graph-node IDs. If OSMnx/Overpass cannot provide a real graph, the API uses the labelled `synthetic_fallback` graph. Do not cite that fallback as actual OSM geometry.

## 5. Start the integrated Streamlit dashboard

```bash
streamlit run dashboard.py
```

The previous dashboard is preserved:

```bash
streamlit run dashboard_legacy.py
```

The new dashboard contains overview, road map/routing, predictions/forecasts, weather, incident simulation, Copilot and model-evaluation tabs. It shows provenance warnings next to synthetic and external data.

## 6. External data sources and constraints

### OpenStreetMap / OSMnx

The provider downloads the Bhubaneswar `drive` network with OSMnx, saves GraphML under `cache/`, and reuses the cache. The OSM driving network retains directionality/one-way restrictions. OSMnx provides geometry, road class, length and available road attributes; missing speeds are imputed from road class. Such speeds are estimates, not measured live traffic. If OSMnx or network access is missing, the provider loads a synthetic fallback graph and labels it accordingly.

### Open-Meteo

The weather adapter requests current modelled conditions for Bhubaneswar coordinates (temperature, rain/precipitation, cloud cover, wind and related fields). It has request timeouts and a short in-memory cache. If the service is unavailable, it returns `unavailable` unless `allow_simulated_fallback=true` is explicitly supplied. Stale cached values and simulated values carry separate status/source labels.

### TomTom

The optional adapter uses TomTom Traffic Flow Segment Data and Incident Details. Set `TOMTOM_API_KEY` to enable the endpoints. **New in v3:** `POST /routes/calculate` with `"use_tomtom_flow": true` (or the dashboard Route Planner checkbox) probes flow along the route corridor, matches each returned segment to *directed* OSM edges by position and travel direction, and re-runs the routing algorithm with the measured speeds on those edges only. The result's `traffic_flow` block states `status` (`applied`, `disabled`, `unavailable`, `no_edge_match`), `is_live`, edges matched and coverage; the route is described as measured only when `status == "applied"`. This is **verified against mocked TomTom responses only**, never against the live service (see `FEATURE_STATUS.md`).

### OSRM

The OSRM adapter is provided for an independent road-route comparison. A valid-coordinate request was attempted during verification but DNS failed; OSRM duration is not treated as live traffic. The public demo endpoint is rate-limited and should not be used as a production service.

## 7. Incident-aware route costs

- Accident, obstruction, hazard and roadwork incidents increase edge time based on severity.
- A road closure removes the affected directed edge from the routing view.
- Reverse-direction effects are only applied when `bidirectional=true`.
- Multiple incidents can coexist on one edge.
- Expired incidents are purged automatically when the incident list/effects are read.
- Incidents can originate from `manual`, `simulation`, or `tomtom`; current UI/API creation is manual or simulation. TomTom *flow* is map-matched for routing (see above); external TomTom *incidents* are still exposed only as raw provider output and are not mapped into the graph.

## 8. Run tests

```bash
pytest -q
```

The automated suite covers ML target alignment, feature leakage exclusions, unknown locations, missing-value handling, chronological split isolation, route optimality (Dijkstra vs A*), closures, incident expiry, weather fallback labels and forecasting feature construction. The release test results are recorded in `TEST_REPORT.md`.

## 9. Docker (optional)

```bash
cp .env.example .env
docker compose up --build
```

API: `http://localhost:8000/docs`  
Dashboard: `http://localhost:8501`

The first OSM network request may take time and depends on public Overpass/Nominatim availability. The app continues with its explicit synthetic fallback if download fails.

## Known limitations before a real-world pilot

1. Traffic, labels and the included historical series are synthetic; no real detector/GPS/travel-time data is bundled.
2. The model has not been prospectively validated using real Bhubaneswar traffic measurements.
3. OSMnx graph download was not executed in the build environment because OSMnx was not installed; install requirements and allow internet access to retrieve real geometry.
4. Live Open-Meteo and TomTom requests were not validated in this run. TomTom also requires an API key.
5. Real traffic flow is not yet automatically map-matched to every OSM road edge.
6. Streamlit UI was not launched in this build environment because Streamlit/streamlit-folium were not installed; source syntax and backend service paths were checked.
7. The LLM Copilot path requires an OpenAI API key and model access. The rule-based fallback is available without a key.
8. In-memory incidents are session/process local; they are not yet shared across multiple API workers or persisted in a database.


## 7. Latest integration verification

The latest end-to-end verification run is documented in `INTEGRATION_AUDIT.md`. The project is an expanded local prototype, not a verified live Bhubaneswar traffic service.

| Component | Latest status |
|---|---|
| Phase 1 classification + persistence baseline | Executed; 28-test suite passes |
| 30/60-minute forecasts | Executed with chronological holdout metrics |
| SARIMA | Executed as a one-location rolling-origin pilot only |
| Dijkstra/A* and accident/closure rerouting | Executed on labelled synthetic fallback graph |
| Real OSMnx Bhubaneswar graph | Implemented, not executed (`osmnx` absent) |
| Open-Meteo | Adapter tests pass; live request attempted but DNS unavailable; fallback is explicitly labelled |
| TomTom | Mocked auth/request construction executed; live provider call not executed (API key absent) |
| FastAPI | Local Uvicorn and endpoint tests executed |
| Streamlit dashboard | Source compiles; UI not launched (`streamlit` packages absent) |
| Copilot | Rule-based fallback executed; OpenAI path untested (key/SDK absent) |
| Docker | Config present; not executed (Docker unavailable) |

Run the test suite and service locally:

```bash
pytest -q
python -m compileall -q .
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# In a second terminal, after installing requirements:
streamlit run dashboard.py
```

Forecasting and optional SARIMA commands are in Sections 3–4 above. Full endpoint outcomes are in `API_TEST_RESULTS.md`; routing/incident execution is in `ROUTING_TEST_RESULTS.md`. Raw logs are in `verification/`. No dashboard screenshots are included because Streamlit could not be launched in the validation environment.

The v3 final suite has **61 tests, 0 warnings, 0 failures** (v2: 28), run in a clean virtual environment with the exact pins in `requirements.txt`; logs are in `verification/v3/`.


Windows setup and live API verification: see [WINDOWS_STARTUP.md](WINDOWS_STARTUP.md).


## 10. Windows deployment and current release readiness

For Windows PowerShell, follow [`WINDOWS_STARTUP.md`](WINDOWS_STARTUP.md). It contains exact virtual-environment, dependency, `.env`, training, API and dashboard commands, plus verification requests for Open-Meteo, TomTom and OpenAI.

The latest deployment-readiness execution is in [`DEPLOYMENT_READINESS_REPORT.md`](DEPLOYMENT_READINESS_REPORT.md). The dashboard status panel distinguishes synthetic traffic, real OSM geometry versus synthetic fallback, weather state, TomTom configuration and Copilot mode. The API `/health` endpoint exposes the same data-source/configuration summary without triggering external requests.

**Current validation boundary:** the dashboard and API were launched and exercised in the build environment (Python 3.12.3), including a real headless Chrome with genuine screenshots. Not verifiable there, and therefore **UNVERIFIED**: the real Bhubaneswar OpenStreetMap download, live Open-Meteo, live TomTom, OpenAI, and Windows/Python 3.14 execution. The app is a synthetic-data demonstrator, not a live Bhubaneswar traffic system. Full details: [`FEATURE_STATUS.md`](FEATURE_STATUS.md).
