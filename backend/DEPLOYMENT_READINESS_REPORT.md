# Deployment Readiness Report — 5 November 2026

> **Historical v2 report.** Written before the v3 final release. Statements here about TomTom mapping, test counts, dashboard launch, screenshots, or dependency/runtime versions are superseded by [`FEATURE_STATUS.md`](FEATURE_STATUS.md).

## Executive result

The expanded project remains intact. The existing ML, forecasting, FastAPI, fallback-routing, incident and rule-based Copilot paths are locally executable. This runtime could not install or launch the Streamlit UI or OSMnx, and it could not reach Open-Meteo because DNS resolution failed. TomTom/OpenAI credentials were not configured. No dashboard screenshots are provided because no dashboard browser session could be launched.

The release is suitable for **local prototype/demo deployment with synthetic traffic and fallback routing**, provided the UI dependencies are installed on the target Windows machine. It is **not yet a verified live Bhubaneswar traffic deployment**.

## Execution evidence

| Area | Status | Evidence / limitation |
|---|---|---|
| ML classification and three-model comparison | WORKING AND EXECUTED | Existing saved RF model loads; persistence, RF and XGBoost metrics reproduced in previous release verification; test suite remains green. |
| 30/60-minute forecasting | WORKING AND EXECUTED | Forecast artifacts are present and inference is covered by tests. |
| Streamlit source compilation | WORKING AND EXECUTED | `python -m compileall -q .` passed. |
| Streamlit UI runtime | IMPLEMENTED BUT NOT EXECUTED | `python -m streamlit run dashboard.py` returned `No module named streamlit`; `streamlit-folium` is also missing. |
| Streamlit dependency installation | NOT EXECUTED | `pip install streamlit streamlit-folium osmnx` could not resolve the package index due DNS/network failure. |
| OSMnx graph acquisition | IMPLEMENTED BUT NOT EXECUTED | Explicit provider load attempted; import failed with `ModuleNotFoundError: No module named 'osmnx'`, then selected labelled fallback. |
| Synthetic fallback graph | SIMULATED | 8 nodes and 24 directed edges returned. It is illustrative, not a surveyed road network; its placeholder links are represented in both directions. |
| Real graph connectivity, geometry and one-way rules | IMPLEMENTED BUT NOT EXECUTED | No real OSM graph was downloaded or cached; therefore these properties were not inspected. |
| Dijkstra/A* and incident rerouting | WORKING AND EXECUTED | Prior release tests demonstrate equivalent route costs and incident/closure rerouting on fallback graph. |
| Open-Meteo request | PARTIALLY WORKING | Actual direct HTTPX request and API request attempted; DNS resolution failed. Adapter returned `status=unavailable`, `is_live=false`, `is_simulated=false` when fallback was disabled. |
| Weather fallback | SIMULATED | Explicit fallback remains separate and labelled; not presented as live weather. |
| TomTom endpoint configuration | PARTIALLY WORKING | Endpoint returned `status=disabled` because `TOMTOM_API_KEY` is absent. Existing mocked tests validate request construction only. |
| TomTom live flow/incident data | IMPLEMENTED BUT NOT EXECUTED | No key was available; endpoint authentication, payload validity and segment coverage remain unverified. |
| TomTom flow to OSM edge mapping | NOT IMPLEMENTED | Adapter returns raw point flow and incident payloads. There is no spatial matching of provider segments to directed OSM edge IDs, nor a live edge-cost update path. |
| OpenAI Copilot | IMPLEMENTED BUT NOT EXECUTED | `OPENAI_API_KEY` and `openai` SDK are absent; actual Responses API tool-calling was not run. |
| Rule-based Copilot | WORKING AND EXECUTED | API chat returned a model-backed prediction summary with `mode=rule_based`. |
| FastAPI runtime | WORKING AND EXECUTED | Uvicorn started; local HTTP requests to health, weather and TomTom endpoints returned 200 with source status in the response body. |
| API source metadata | WORKING AND EXECUTED | `/health` now reports data-source state and configuration without making external calls. `/routing/nodes` returns `graph_source` and `is_real_road_geometry`. |
| Automated tests | WORKING AND EXECUTED | `28 passed`. |
| Python compilation | WORKING AND EXECUTED | `python -m compileall -q .` passed. |
| Windows startup guide | WORKING AND EXECUTED | `WINDOWS_STARTUP.md` added with PowerShell venv, dependency, `.env`, training, API, dashboard and provider-check commands. |

## Environment blockers observed

- Python: 3.13.5 in the validation runtime.
- Missing optional packages: `streamlit`, `streamlit_folium`, `osmnx`, `openai`.
- `pip install` could not retrieve the packages because the package index hostname could not be resolved. The earlier installation attempt showed `Temporary failure in name resolution`.
- Open-Meteo `https://api.open-meteo.com/v1/forecast` failed with `httpx.ConnectError: [Errno -3] Temporary failure in name resolution`.
- `TOMTOM_API_KEY`: not set.
- `OPENAI_API_KEY`: not set.
- No dashboard screenshot is included: Streamlit did not start, so screenshots of its tabs would be fabricated.

## TomTom/OSM road-edge integration assessment

The present TomTom Flow Segment Data adapter accepts a latitude/longitude point and returns a raw provider response. The route optimizer, by contrast, applies costs keyed to graph edges. There is no bridge that:

1. obtains a provider traffic segment geometry/polyline,
2. spatially matches it to an OSM `MultiDiGraph` directed edge (including parallel edges and direction),
3. validates freshness and quality/coverage,
4. converts current-vs-free-flow speed or confidence into a bounded edge travel-time cost, and
5. injects that edge-cost overlay into Dijkstra/A*.

Accordingly, **TomTom is not integrated into route optimization**. Do not describe the current build as live-traffic-aware. Implementing and validating this bridge should be a distinct follow-on task after the target environment has working OSMnx and a valid TomTom key.

## Data-source behavior after the update

- `/health`: returns `data_sources` for traffic, road network, weather, TomTom and Copilot. It reports `not_requested` for weather because health does not issue network calls; TomTom/OpenAI report configuration, not successful authentication.
- `/traffic/current`, `/traffic/predictions`, `/forecasts/{location}`: synthetic/historical provenance (`is_live=false`).
- `/routing/nodes`: returns graph source and real-geometry boolean. `synthetic_fallback` must be shown as simulated geometry.
- `/weather/current`: returns `ok` for a successful external response, `stale_cache` for stale cached data, `simulated_fallback` only when requested, or `unavailable` when no source works.
- Dashboard Overview now loads the graph before rendering provenance, displays the active graph source, and warns when fallback geometry is in use. Weather labels distinguish live, stale cache, simulated fallback and unavailable. The Copilot panel shows which execution mode answered.

## Exact next steps on a Windows workstation

Follow `WINDOWS_STARTUP.md`. In brief:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
pytest -q
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

In a second activated terminal:

```powershell
streamlit run dashboard.py
```

Then verify `/routing/nodes` says `openstreetmap_downloaded` or `openstreetmap_cached` and `is_real_road_geometry=true` before demonstrating real roads. Call `/weather/current?force_refresh=true` and check `status=ok` / `is_live=true`. Test TomTom after setting a key and require `status=ok` plus a useful payload. Test Copilot with an OpenAI key and confirm `mode=openai_function_calling`. Those checks were **not** possible in this runtime.

## Final release decision

**Prototype deployment: ready with limitations. Live-provider and real-map deployment: not verified.** Existing working features have been preserved. The new release manifest enumerates every packaged file with SHA-256. No credentials or `.env` file are included.
