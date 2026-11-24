# What each number on screen is

| Label in UI | Meaning | Is it live? |
|---|---|---|
| **Historical synthetic · not live** | Latest row of the bundled synthetic Bhubaneswar dataset (`/traffic/current`) | **No** |
| **ML prediction · synthetic input** | Classifier output (next 30-minute class) computed from those synthetic lag values | No |
| **Forecast model output** | 30/60-minute regressors + congestion class from synthetic history | No |
| **OpenStreetMap geometry** | Real OSM road graph from the local cache | Static map data |
| **Synthetic fallback graph · not OSM** | 8-node illustrative graph used when no OSM cache exists | No |
| **Open-Meteo · live model weather** | Model-derived current conditions (not roadside sensors); cached up to the backend TTL | Near-live |
| **SIMULATED weather** | Generated demo values; only on explicit request | No |
| **TomTom · live measurement** | Measured flow, only when a key is configured on the backend | Yes |
| **Simulated / manual incident** | In-memory record created by a user/simulation; expires automatically | No |
| **Rule-based fallback / LLM · tool-grounded** | Copilot mode reported by the backend | – |
| **Saved training artifacts** | Metrics from a chronological holdout of the synthetic data | No |

Marker positions for the 10 synthetic locations are **approximate demo anchors** from `backend/config/location_coordinates.json`
(imported by the frontend, not copied). They are not surveyed sensor sites. Evaluation metrics describe synthetic data,
**not accuracy on real road traffic**.
