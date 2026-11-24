# Integration & Verification Report

Build date: 2026-11-12. Sources: `traffic_bottleneck_v3_final_2026-11-10.zip` (backend, authoritative) and
`project-bolt-sb1-ammd7kex (2).zip` (frontend, authoritative). Logs and screenshots: `docs/verification/`.

## 1. Result in one paragraph
The React frontend now talks to the v3 FastAPI backend on all nine pages. Backend: 74/74 tests pass on Python 3.12.3 **and**
3.14.10 (Linux). Live HTTP smoke test: 27/27. Real headless-Chromium browser test of the running app: 49/49, from both
`127.0.0.1:5173` and `localhost:5173`, with no page errors and no unexpected console errors. **Not executed:** anything on
Windows itself, the real OpenStreetMap graph, live Open-Meteo/TomTom/OpenAI, and place-name geocoding (section 5).

## 2. What was changed
### Backend (diff vs ZIP 1 is exactly this)
| File | Change |
|---|---|
| `backend/backend/main.py` | CORS: added `localhost`/`127.0.0.1` on 5173 and 4173 (8501 retained) + optional `CORS_ALLOW_ORIGINS`; added read-only `GET /models/evaluation`. |
| `backend/tests/test_react_frontend_integration.py` | new, 13 tests (CORS, evaluation endpoint, lat/lon fields, one-endpoint-pair validator, incident→reroute→delete). |
| `backend/RELEASE_MANIFEST.json` | renamed `RELEASE_MANIFEST.zip1_original.json` (it describes ZIP 1 and is stale for the changed `main.py`); the new top-level manifest supersedes it. |

Everything else is byte-identical to ZIP 1 (checked with `diff -r`). **No model was retrained or modified; no model behaviour changed.**
The added endpoint is the only API addition, is read-only, and exists because no endpoint exposed evaluation metrics.

### Frontend
Kept: dark navy/cyan design, sidebar, layout, UI primitives, Leaflet map, Recharts, all nine pages and routes.
Replaced: all mock data (`src/mock-data/` deleted), `lib/api.ts` (single API client), `types/` (real schemas), page logic.
Added: `useApi` hook, provenance tags/banners, long routing timeout, map-click / place-name route modes.
Fixed: Leaflet CSS now bundled (was loaded from unpkg, which breaks offline); Leaflet animations disabled (a navigate-away race threw `_leaflet_pos`).

### Removed because the backend cannot supply it (not replaced with invented numbers)
24-hour trend, weekly/daily forecast views and confidence bands, 12-hour weather impact chart and advisory, LSTM/Prophet/ARIMA comparison,
training-loss curve, feature-importance chart, incident title/status/location workflow, fake topbar search and notification dot,
hard-coded "Operational" badge. Pages show a labelled "Not available from the backend" note where relevant.

## 3. Executed verification
| Check | Result | Evidence |
|---|---|---|
| Baseline: untouched ZIP 1 backend | 61 passed (Python 3.12.3) | run before any change |
| Backend `compileall` | OK | — |
| Backend pytest, Python 3.12.3 | **74 passed** | `backend_pytest_py312.log` |
| Backend pytest, **Python 3.14.10**, pinned deps, `InconsistentVersionWarning` as error | **74 passed** | `backend_pytest_py314.log`, `environment.txt` |
| Pinned requirements install on 3.14.10 | OK (scikit-learn 1.9.1, xgboost 3.4.1, numpy 2.5.4, pandas 3.0.6) | `environment.txt` |
| Model artifact loading (RF classifier, 30/60-min regressors) | OK (covered by `test_artifact_compat`, smoke test) | pytest logs |
| Live API smoke test on 3.14.10 (health, CORS pre-flight, locations, current, predictions, predict, forecast 30/60 × RF/XGB, nodes, Dijkstra, A*, alternatives, coordinate route, validator 422, incident create/reroute/delete/404, weather, TomTom, Copilot, evaluation, OpenAPI) | **27/27** | `smoke_test_api_py314.log` |
| Frontend `tsc --noEmit` / `eslint .` / `vite build` | exit 0 / 0 / 0 (build warns about a >500 kB chunk; harmless) | `frontend_*.log` |
| Browser E2E (headless Chromium 153) against the running stack, backend on 3.14.10 | **49/49** | `e2e_browser_run.log`, `e2e_browser_result.json`, `screenshots/` |
| Same suite from origin `localhost:5173` (earlier run, 48 checks before the name-mode check was added) | 48/48 | — |

The browser test covers: all nine pages load real data; location markers; layer switch; prediction + what-if; 30/60-min forecasts for RF and XGBoost;
**route calculation through the React UI** (3 ranked alternatives, polyline drawn, Dijkstra and A*, coordinate mode by map click);
**incident creation and deletion** with the route visibly re-planned (`1→3→8` became `1→5→8`) and invalid-edge error shown;
weather *unavailable* and labelled *simulated* states; Copilot answers with expandable tool results; model evaluation values and model switching;
sidebar navigation; mobile viewport; backend-down error state. API responses were all 2xx except two deliberate negative cases
(invalid incident edge → 400; place-name geocoding offline → 400, shown as a readable error).

## 4. Findings you should know about
1. **First routing request with no OSM cache downloads the whole city inside the request** (backend behaviour, unchanged). The UI allows 240 s and says so;
   run `scripts\download_osm.bat` first. Without a cache the app uses the 8-node synthetic fallback graph and labels it "not OpenStreetMap".
2. The 10 traffic "locations" are synthetic archetypes with **no coordinates in any API**; map markers use the approximate anchors in `backend/config/location_coordinates.json`.
3. `/traffic/current` is the *latest dataset row* — there is no live-traffic or history endpoint. This is why pages say "not live".
4. Evaluation metrics are for the synthetic holdout; they are not real-road accuracy.
5. Unused `@supabase/supabase-js` dependency left in `package.json` (no code uses it); the legacy Streamlit dashboard is left in `backend/`.

## 5. NOT verified (be explicit)
* **Windows:** the `.bat` scripts, `py -3.14`, PowerShell and Windows paths were **not executed** (no Windows machine). Python 3.14 was tested on **Linux**.
  The backend's own earlier notes record that all pinned packages have `win_amd64` cp314 wheels; I did not re-verify that.
* **Real OpenStreetMap graph:** no internet in the build sandbox. The real-OSM path in the browser (large graph, real polylines, node picker over a big graph) was not exercised;
  the backend's mock-Overpass tests passed.
* **Live external services:** Open-Meteo (live), TomTom, OpenAI function-calling, and Nominatim place-name geocoding were not reachable/configured. Only their offline/disabled/fallback behaviour was tested.
* **Map tiles and Google Fonts** could not load offline, so the visual with tiles/Inter font was not seen (system fonts and a blank tile layer were used in screenshots).
* **Browsers:** only headless Chromium 153. Firefox/Safari untested.
* Frontend has no unit-test framework (none existed); browser E2E is the frontend test.
* The fresh-extraction acceptance run of the final ZIP is reported separately in the hand-off message (it cannot be recorded inside the ZIP it verifies).
