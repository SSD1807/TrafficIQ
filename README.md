# Traffic Bottleneck Final — React dashboard + FastAPI backend

One runnable application: the **Bolt React/TypeScript/Vite frontend** (nine dashboard pages) wired to the
**authoritative FastAPI backend v3** (ML classifier, XGBoost, 30/60-min forecasting, OSMnx routing with
Dijkstra/A*, incident-aware rerouting, Open-Meteo, TomTom, Copilot).

> **Read this first — data honesty.** All traffic values are a **historical synthetic dataset**, not live traffic.
> ML predictions/forecasts are model outputs on that synthetic history. Every page labels what it shows
> (synthetic / ML / forecast / OSM / weather / TomTom / simulated incident / rule-based Copilot).
> See `docs/DATA_PROVENANCE.md`.

## Layout
```
Traffic_Bottleneck_Final/
  backend/            FastAPI project root (kept intact so imports and artifact paths keep working)
    backend/          FastAPI package  (backend.main:app)
    models/           trained model artifacts (do not retrain; versions are pinned)
    dataset/          bundled synthetic dataset
    outputs/          saved metrics, forecasting artifacts
    routing/ forecasting/ services/ copilot/ ...   backend source (unchanged)
    tests/            backend tests (230 + 1 opt-in live test)
    requirements.txt  PINNED dependencies (authoritative)
  frontend/           React + Vite app (src/, package.json)
  tests/e2e/          browser end-to-end test (Playwright)
  scripts/            smoke_test_api.py, verify_release.py, download_osm.bat, run_tests.bat
  docs/               report, API map, provenance, verification logs + screenshots
  .env.example  setup_windows.bat  start_backend.bat  start_frontend.bat  start_project.bat
  RELEASE_MANIFEST.json  SHA256SUMS.txt
```
Why `backend/` holds `models/`, `dataset/`, `tests/`: the backend resolves them relative to its own root
(`ROOT/models`, `ROOT/dataset`, `cache/...` relative to the working directory). Keeping ZIP 1 intact avoids
touching code paths. Nothing is duplicated.

## Quick start (Windows 11, PowerShell or Explorer)
Prerequisites: **Python 3.14 (64-bit, with the `py` launcher; 3.12 also works)** and **Node.js 18+**.
```powershell
.\setup_windows.bat        # one time: project-local .venv, pinned pip install, npm install
.\start_project.bat        # starts backend (8000) + frontend (5173) and opens the browser
```
Or separately: `start_backend.bat` / `start_frontend.bat`.
URLs: app http://127.0.0.1:5173 · API http://127.0.0.1:8000 · API docs http://127.0.0.1:8000/docs

### Real OpenStreetMap roads (recommended, needs internet, one time, ~1–5 min)
```powershell
.\scripts\download_osm.bat
```
The project ships with a cached real OSM graph (`backend\cache\bhubaneswar_drive.graphml`). If that file is missing or
unreadable the API returns **HTTP 503 with the reason and fix** and the Route Planner shows a red "road network not
available" banner - it **never silently switches to a synthetic network**. Check it any time: http://127.0.0.1:8000/network/status

### Manual / macOS / Linux
```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt             # keep scikit-learn 1.9.1 / xgboost 3.4.1 pinned
cp .env.example backend/.env                        # optional keys (see below)
cd backend && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000     # terminal 1
cd frontend && cp .env.example .env && npm install && npm run dev                 # terminal 2
```

## Configuration
* `frontend/.env`: `VITE_API_BASE_URL=http://127.0.0.1:8000` (the only frontend setting).
* `backend/.env` (copy of `.env.example`): optional `OPENAI_API_KEY`, `TOMTOM_API_KEY`, `OSM_PLACE_QUERY`,
  `CORS_ALLOW_ORIGINS`, and the `TOMTOM_INCIDENTS_*` settings (see `docs/LIVE_INCIDENTS.md`). **Never put keys in `frontend/.env` or any `VITE_*` variable** (they are public in the bundle).
* CORS allows `localhost`/`127.0.0.1` on 5173 (dev), 4173 (preview), 8501 (legacy Streamlit).

## Incident-aware routing, location search, readable road names (new)
* **Smart Route Planner** (`/routes/plan`): type *Jharapada, Bhubaneswar* -> *Baragada, Bhubaneswar*; places are searched in the
  loaded OSM road names and on OpenStreetMap Nominatim (India + city bounding box, spelling variants), snapped to the nearest
  drivable node, routed with Dijkstra or A*, checked against live TomTom incidents, and re-routed automatically when a confirmed
  closure blocks the route or an incident costs more than the configured threshold. Blue = normal route, green = recommended
  alternative, red = closure, orange = accident, yellow = construction, dashed amber = unmatched incident.
* **Incident Management**: same engine - readable road names, impact class, matched/unmatched with the reason, routing effect.
* Rules, thresholds and the exact decision logic: `docs/INCIDENT_AWARE_ROUTING.md`.
* Optional `.env` keys: `NOMINATIM_USER_AGENT` (set your e-mail), `MAX_NODE_SNAP_DISTANCE_M`, `INCIDENT_IMPACT_RULES`
  (JSON file). Needs internet: Nominatim (place search), TomTom (`TOMTOM_API_KEY`, live incidents), Overpass (only for
  `scripts\download_osm.bat`). Without internet the app still routes on the cached OSM graph and clearly reports which
  services are unavailable.
* Known data limit: only about 6 % of the road edges in the bundled cache carry an OSM `name`; other junctions are labelled
  "Near <road>" or by coordinates until reverse geocoding (Nominatim) can name them.

## Live incident detection (TomTom) - new
Real traffic incidents from the **TomTom Traffic Incident Details API (v5)** are fetched by the backend, matched to
directed OpenStreetMap road edges and applied to routing (closures remove the edge, accidents/works/hazards add
severity-scaled cost; Dijkstra and A* both honour them). Manual and simulated incidents keep working. Full details,
status values, matching method, limitations and measured matching accuracy: **`docs/LIVE_INCIDENTS.md`**.

Set it up (the key stays in the backend, never in the browser):
1. Get a key at https://developer.tomtom.com (Traffic API enabled).
2. Open `backend\.env` and set `TOMTOM_API_KEY=your_key_here` (no quotes). Optional: `TOMTOM_INCIDENTS_ENABLED`,
   `TOMTOM_INCIDENTS_POLL_INTERVAL` (default 60, minimum 30), `TOMTOM_INCIDENTS_BBOX` (see `.env.example`).
3. Restart the backend (`start_backend.bat`), open **Incident Management**. Status shows `Live` / `Live - no incidents`
   when TomTom answers, `Disabled - no TomTom API key` without a key. An empty feed is a valid result.

Verify the live connection (opt-in; uses your TomTom quota; normal tests never do):
```powershell
cd backend
$env:RUN_LIVE_TOMTOM_TESTS="1"; ..\.venv\Scripts\python.exe -m pytest tests\test_tomtom_incidents_live.py -s -v
```
(cmd.exe: `set RUN_LIVE_TOMTOM_TESTS=1`.) Or call `POST http://127.0.0.1:8000/incidents/refresh` and read `provider.state`.

## Tests
```powershell
.\scripts\run_tests.bat                      # backend pytest + frontend typecheck/lint/unit tests/build
cd frontend; npm test                        # frontend unit tests only (Vitest)
python scripts\smoke_test_api.py             # live API smoke test (backend must be running)
cd tests\e2e; npm install; npx playwright install chromium; node e2e.mjs   # browser test (both servers running)
python scripts\verify_release.py             # verify files against SHA-256 manifest
```
What was actually run, with logs: `docs/INTEGRATION_AND_VERIFICATION_REPORT.md` and `docs/verification/`.

## Pages → backend endpoints
See `docs/API_INTEGRATION_MAP.md`. Pre-existing backend docs remain in `backend/` (README, WINDOWS_STARTUP.md, ...).
The old Streamlit dashboard is still in `backend/` (`dashboard.py`) and is unaffected.
