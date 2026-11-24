# Dashboard v3 — what changed and how to use it

> Final status of every feature (verified / mock-verified / unverified): see [`FEATURE_STATUS.md`](FEATURE_STATUS.md).

Run: `streamlit run dashboard.py` (API unchanged: `uvicorn backend.main:app --reload`).
Previous dashboards are preserved: `dashboard_v1.py` (the one you ran before) and `dashboard_legacy.py` (original).

## First run: load real Bhubaneswar roads
The dashboard starts instantly on the labelled **synthetic fallback** graph if no cache exists. Then either:
1. Open **Traffic Map → Road network panel → Download Bhubaneswar road network** (one-time, minutes), or
2. `python scripts/download_osm.py`, or, if Overpass is blocked on your network,
3. export an `.osm` file from openstreetmap.org and use the panel's uploader or `python scripts/download_osm.py --file my.osm`.

The graph is cached at `cache/bhubaneswar_drive.graphml`; later starts load it offline. The download tries the Bhubaneswar boundary first, then a 9 km radius around the centre.

## Sections
| Section | What it shows |
|---|---|
| Executive Overview | health, locations, congestion distribution, predicted High, avg speed, incidents, weather, road network, per-source provenance badges |
| Traffic Map | OSM roads (names, type, one-way arrows), road-type or **simulated** congestion colouring, ML-predicted location markers, incidents/closures, route + alternatives, click-to-set origin/destination, legend, fullscreen, locate-me, basemap switch |
| Traffic Prediction | per-location class, probabilities, confidence, what-if overrides, 24 h history, global feature importance, hold-out model comparison |
| Forecasting Analytics | 30/60-min vehicle count, speed, class probabilities; RF vs XGBoost regressor; actual-vs-forecast charts; MAE/RMSE/SMAPE comparisons |
| Route Planner | origin/destination by monitored location, OSM road-name search, map pick or lat/lon; Dijkstra/A*; alternatives; turn-by-turn road steps; departure time; rain; Dijkstra-vs-A* comparison |
| Incident Lab, Weather, AI Copilot, Model Evaluation | preserved and restyled |

## Provenance badges (never relaxed)
LIVE DATA = successful external response flagged live · SYNTHETIC DATA = bundled/replayed/simulated · CACHED DATA = stale or cached external result · UNAVAILABLE = not configured or failed · OSM · STATIC = real OpenStreetMap geometry, which is a map, not traffic.

## Design decisions to know about
* Monitored-location coordinates (`config/location_coordinates.json`) are **approximate demo anchors**: the dataset's ten names are generic and synthetic. Edit them freely.
* Road colouring by "Simulated congestion" is the existing deterministic scenario model, not measured traffic.
* Only the Random Forest classifier is saved as an artifact, so the Prediction page cannot run XGBoost live; its hold-out metrics are shown. Forecast regressors (RF and XGBoost) can both be run.
* Theme follows Streamlit's setting (⋮ → Settings → Theme).

## Added in the final v3 pass
* **TomTom measured flow (Route Planner checkbox, API `use_tomtom_flow`)** — probes along the route corridor, matches segments to directed OSM edges, re-routes with measured speeds on matched edges. Disabled without `TOMTOM_API_KEY`. Shows `LIVE DATA` only when `traffic_flow.status == "applied"`. Mock-verified only.
* One-way arrows are rotated markers (the earlier plugin crashed the map under the canvas renderer).
* `scripts/download_osm.py [--file x.osm]`, `scripts/render_metric_tables.py [--check]`, `scripts/generate_manifest.py`, `scripts/verify_manifest.py`.
* `screenshots/` (genuine captures; read its README for the test-fixture caveat) and `tools/screenshot_capture/` (the Node scripts that made them).
