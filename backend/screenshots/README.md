# Screenshots — how they were made and what they show

All images are **genuine captures** of the running Streamlit dashboard (`streamlit run dashboard.py`, Streamlit 1.66.1) taken with real
headless Chrome 153 (`tools/screenshot_capture/shoot.mjs`). Nothing is mocked up or edited. Every run reported **0 JavaScript page errors**
(`verification/v3/browser_capture_*.log`).

## Capture-environment limits (be aware when reading the images)
* The capture machine has **no internet**. Therefore: (1) base-map tiles are replaced by a flat neutral colour, (2) Open-Meteo is unreachable so
  Weather honestly shows **UNAVAILABLE**, (3) the real Bhubaneswar OSM download could not run, so the default state uses the labelled
  **synthetic fallback graph** (8 illustrative junctions), (4) the TomTom checkbox is disabled because no key is configured.
* Map JavaScript/CSS libraries (Leaflet etc.) are normally fetched from public CDNs; for the capture they were served from the same npm
  packages locally. On your machine they load from the CDN as usual.

## Folders
| Folder | Contents |
|---|---|
| `default_state/` | Overview, Traffic Map, Prediction, Forecasting, Route Planner (after "Find best route"), Incident Lab (after adding an incident), Weather (after "Fetch"), AI Copilot (after asking a question), Model Evaluation — the app exactly as delivered, fallback graph |
| `osm_code_path_TEST_FIXTURE_ONLY/` | The **real OSMnx-parsed graph code path** (road names, one-way arrows, `OSM · STATIC` badge, location markers, click-to-set origin/destination) using a tiny **test-only 4×4 grid** (`tests/fixtures/mini_grid.osm`). **This is NOT Bhubaneswar geometry.** Do not use these images as if they were the city map. |
| `dark_theme/` | Overview, Traffic Map, Route Planner with Streamlit's dark theme |

Real Bhubaneswar road geometry has **not** been seen in any screenshot because the download needs internet. After `python scripts/download_osm.py`
on your own machine, re-run the dashboard and take your own screenshots for the report.
