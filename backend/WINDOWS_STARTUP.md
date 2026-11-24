# Windows 10/11 — Startup, Verification and Troubleshooting (v3 final)

Run every command in **PowerShell** from the extracted project root (the folder containing `requirements.txt`).

## What has and has not been verified for Windows
| Item | Status |
|---|---|
| Every package in `requirements.txt` (and all 87 resolved dependencies) has a **prebuilt `win_amd64` wheel for CPython 3.14** | **Verified** with `uv pip compile --python-platform windows --python-version 3.14 --only-binary :all:` (`verification/v3/requirements.windows-cp314.resolved.txt`) |
| The same pinned versions install and pass all tests on Linux, Python 3.12.3 | **Verified** (`verification/v3/`) |
| The command **sequence below** (venv → pip → tests → API → dashboard → OSM script) | **Verified on Linux** with the POSIX equivalents (`verification/v3/startup_check.log` is produced outside the ZIP; see FEATURE_STATUS.md) |
| `py -3.14`, `.\.venv\Scripts\Activate.ps1`, `Copy-Item`, `Invoke-RestMethod`, execution-policy behaviour | **Not executed** — there is no Windows machine in the build environment |
| Python 3.14 itself running this project | **Not executed** (tested on 3.12.3). Use 3.12 if anything fails on 3.14 |

## 1. Virtual environment (Python 3.14 or 3.12, 64-bit)
```powershell
py -3.14 --version            # or: py -3.12 --version
py -3.14 -m venv .venv        # or: py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```
If PowerShell blocks activation: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again.

## 2. Install the pinned dependencies
```powershell
python -m pip install -r requirements.txt
python -c "import sklearn, xgboost, numpy, pandas, streamlit, osmnx; print('scikit-learn', sklearn.__version__); print('xgboost', xgboost.__version__); print('numpy', numpy.__version__); print('pandas', pandas.__version__); print('streamlit', streamlit.__version__); print('osmnx', osmnx.__version__)"
```
Expected: scikit-learn **1.9.1**, xgboost **3.4.1**, numpy 2.5.4, pandas 3.0.6, streamlit 1.66.1, osmnx 2.1.1.

**Do not change scikit-learn/xgboost versions.** The saved models were trained with exactly these versions; a different scikit-learn raises
`InconsistentVersionWarning` (and may give wrong results). If you must change versions, retrain first (section 4) and update the pins.

## 3. Configure optional keys
```powershell
Copy-Item .env.example .env
notepad .env
```
`OPENAI_API_KEY` (optional Copilot LLM), `TOMTOM_API_KEY` (optional measured flow), `OSM_PLACE_QUERY`. Without keys everything still runs;
the Copilot is rule-based and TomTom features are disabled. `.env` must never be shared.

## 4. (Optional) Reproduce the models
```powershell
python phase1_preprocess.py
python phase2_train.py
python -m forecasting.forecast_pipeline
python scripts/render_metric_tables.py      # re-syncs numbers in the Markdown reports
```
Each run writes the library versions into `models/model_metadata.json` and `outputs/forecasting/forecast_metrics.json`;
`tests/test_artifact_compat.py` fails if they differ from the installed versions.

## 5. Tests
```powershell
pytest -q
python -m compileall -q .
```
Expected: **61 passed**, no warnings. (The real-browser screenshot scripts in `tools/screenshot_capture/` are separate Node tools, not part of pytest.)

## 6. Load the real Bhubaneswar road network (once)
```powershell
python scripts/download_osm.py
```
Needs internet (Nominatim + Overpass); may take several minutes. It caches `cache\bhubaneswar_drive.graphml`; later runs are offline.
If blocked: export an `.osm` file from openstreetmap.org (Export) and run `python scripts/download_osm.py --file path\to\export.osm`.
Until a real graph is loaded the dashboard shows the **synthetic fallback**, clearly labelled — never present that as Bhubaneswar.

## 7. Start the API (terminal 1) and the dashboard (terminal 2)
```powershell
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
```powershell
streamlit run dashboard.py
```
Browse to `http://127.0.0.1:8000/docs` and `http://localhost:8501`. The previous dashboard remains available:
`streamlit run dashboard_v1.py` (v1) and `streamlit run dashboard_legacy.py` (original).
`--reload` can be added to uvicorn for development.

## 8. Quick API checks
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 6
Invoke-RestMethod http://127.0.0.1:8000/traffic/current | ConvertTo-Json -Depth 4
Invoke-RestMethod "http://127.0.0.1:8000/forecasts/Bus%20Stand?horizon_minutes=30&model=xgboost" | ConvertTo-Json -Depth 4
Invoke-RestMethod "http://127.0.0.1:8000/weather/current?force_refresh=true" | ConvertTo-Json -Depth 6
```
Weather is live only if the result says `status: ok` **and** `is_live: true`.

## 9. TomTom measured flow (needs `TOMTOM_API_KEY`) — read before claiming "live traffic"
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/routes/calculate -ContentType "application/json" -Body '{"origin_node":"<id>","destination_node":"<id>","use_tomtom_flow":true}' | ConvertTo-Json -Depth 8
```
(Get node ids from `/routing/nodes`; the dashboard Route Planner has the same option as a checkbox.) Inspect `traffic_flow`:
* `status: "applied"` and `is_live: true` → measured TomTom speeds were matched to directed OSM edges and used by the routing algorithm on
  those edges only (`edges_matched`, `coverage_best_route`, per-route `measured_distance_share`). Everything else is still modelled.
* `disabled` / `unavailable` / `no_edge_match` → **no measured data was used**; costs are modelled/simulated. Do not call the result live.

**This integration is verified only against mocked TomTom responses** (`tests/test_tomtom_routing.py`). It has never been run against the real
TomTom service (no key, no internet in the build environment). It assumes TomTom returns the segment polyline ordered in the direction of flow
and within about 35 m of the OSM centre-line. Probing covers only the baseline route corridor (default ≤ 24 calls per route request).

## 10. Provenance rules for demos
* Bundled traffic observations and all model metrics are **synthetic**, dated 2023-03-14; they are not current traffic.
* OpenStreetMap is road geometry and attributes only — not traffic.
* Colour-coded road congestion and default route times are a **simulated** scenario model unless `traffic_flow.status == "applied"`.
* Monitored-location map positions (`config/location_coordinates.json`) are approximate demo anchors, not sensor locations.

## Troubleshooting
* *pip tries to compile something / fails on Windows* → confirm 64-bit Python 3.12 or 3.14 (`python -c "import struct; print(struct.calcsize('P')*8)"` must print 64).
* *`InconsistentVersionWarning`* → scikit-learn/xgboost differ from the pins; reinstall from `requirements.txt` in a fresh venv.
* *Map is blank* → open the browser console; the map loads Leaflet from public CDNs, so a firewall that blocks `cdn.jsdelivr.net`, `cdnjs.cloudflare.com` or `code.jquery.com` breaks it.
* *Weather shows UNAVAILABLE* → `api.open-meteo.com` is unreachable; the dashboard never invents weather values.
