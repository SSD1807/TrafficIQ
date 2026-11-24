# Test and validation report

> **Historical v2 report.** Written before the v3 final release. Statements here about TomTom mapping, test counts, dashboard launch, screenshots, or dependency/runtime versions are superseded by [`FEATURE_STATUS.md`](FEATURE_STATUS.md).

## Current verification run

Executed from the expanded project root on Python 3.13.5:

```bash
pytest -q
python -m compileall -q .
python phase1_preprocess.py
python phase2_train.py
python -m forecasting.forecast_pipeline
python -m forecasting.sarima_runner --max-locations 1 --max-test-points 24
```

- Automated suite: **28 passed**.
- Syntax compilation: passed.
- Phase 1 preprocessing and training: executed, reproduced metrics.
- 30/60-minute forecasting: executed, regenerated model artifacts, JSON metrics and plots.
- SARIMA: bounded one-location pilot executed.
- Local FastAPI: Uvicorn started and HTTP smoke requests succeeded.
- TestClient: 31 endpoint cases exercised.
- Streamlit UI launch: not possible; `python -m streamlit run dashboard.py` returned `No module named streamlit`.
- Added health endpoint data-source metadata test; total suite is now 28 passing tests.

Logs and response snapshots are stored in `verification/`.

## ML classification (synthetic dataset only)

| Model | Accuracy | Macro-F1 | High precision | High recall | High FP | High FN |
|---|---:|---:|---:|---:|---:|---:|
| Persistence | 91.24% | 78.71% | 51.05% | 51.05% | 93 | 93 |
| Random Forest | 96.06% | 88.03% | 64.71% | 75.26% | 78 | 47 |
| XGBoost | 95.10% | 86.78% | 52.07% | 92.63% | 162 | 14 |

Random Forest remains selected based on validation macro-F1. The same chronological holdout test window (5,260 examples) was used for all three. Metrics do not indicate real-world Bhubaneswar performance.

## Forecasting

The panel forecast pipeline evaluated persistence, moving average, Random Forest and XGBoost on the same 7,000-row chronological holdout per horizon. Detailed MAE, RMSE, SMAPE, classification reports, confusion matrices and plots are in `FORECASTING_REPORT.md` and `outputs/forecasting/forecast_metrics.json`.

A future-perturbation test verifies that changing future measurements does not alter features at an earlier forecast origin.

## Routing and incident verification

Dijkstra and A* returned the same optimal route cost for three pairs on the synthetic fallback graph. A severity-1 accident and a road closure each rerouted the 1→8 path from `1→3→8` to `1→5→8`. Full metrics and provenance are in `ROUTING_TEST_RESULTS.md`.

## External services and application UI

- Open-Meteo parsing/cache/fallback tests use mocked HTTP responses and pass. A real request was attempted but DNS resolution failed.
- TomTom live calls were not made because `TOMTOM_API_KEY` is absent; mocked adapter tests validate auth parameter wiring and documented request parameters.
- OSRM was attempted and failed DNS resolution.
- OpenAI Copilot tool-calling was not called; the API key and SDK were absent. The rule-based fallback is tested.
- OSMnx is absent; only the synthetic fallback graph ran.
- Streamlit and streamlit-folium are absent; the dashboard was not launched and there are no screenshots.
- Docker CLI is absent; container build/start was not run.

See `INTEGRATION_AUDIT.md` for exact feature statuses and `API_TEST_RESULTS.md` for endpoint-by-endpoint results.


For the 5 November 2026 deployment-readiness checks (missing UI/OSM packages, Open-Meteo DNS failure, absent TomTom/OpenAI credentials, API provenance and Windows setup), see `DEPLOYMENT_READINESS_REPORT.md` and `verification/deployment_readiness/`.
