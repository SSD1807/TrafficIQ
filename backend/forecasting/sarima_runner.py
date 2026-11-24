"""Optional rolling-origin SARIMA seasonal benchmark for the traffic panel.

SARIMA is run separately because a univariate model is trained per location and
target, not as a shared panel estimator. The model is fit on the chronological
training segment. At each holdout origin it predicts 1 and 2 steps ahead; after
scoring the origin, the observed value is appended to the state without
refitting. It is intentionally reported separately from the shared panel models.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from forecasting.forecast_pipeline import regression_metrics, ROOT


def run(source: Path | None = None, max_locations: int | None = None, max_test_points: int = 48) -> dict:
    source = source or ROOT / "dataset" / "bhubaneswar_synthetic_traffic.csv"
    raw = pd.read_csv(source, parse_dates=["timestamp"])
    report = {"metadata": {
        "source": str(source), "domain": "synthetic Bhubaneswar data only",
        "model": "SARIMAX(1,0,0) x (1,0,0,48)",
        "evaluation": "rolling-origin holdout; fit on first 80% of each location series, predict 30 and 60 minutes from each origin, then append observed values without refitting",
        "test_points_per_location": int(max_test_points),
        "warning": "SARIMA is a per-location univariate baseline and should be interpreted separately from shared panel regressors; this optional runner can be computationally expensive.",
    }, "results": {}}
    locations = sorted(raw.location.astype(str).unique())
    if max_locations:
        locations = locations[:max_locations]
    for target in ["vehicle_count", "avg_speed"]:
        per_horizon = {"30m": {"actual": [], "predicted": [], "locations": []},
                       "60m": {"actual": [], "predicted": [], "locations": []}}
        for location in locations:
            frame = raw[raw.location.astype(str) == location].sort_values("timestamp").reset_index(drop=True)
            series = pd.to_numeric(frame[target], errors="coerce").dropna().reset_index(drop=True)
            if len(series) < 120:
                continue
            cut = int(len(series) * 0.80)
            train, test = series.iloc[:cut], series.iloc[cut:cut + max(2, int(max_test_points))]
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    fitted = SARIMAX(
                        train, order=(1, 0, 0), seasonal_order=(1, 0, 0, 48), trend="c",
                        simple_differencing=True, enforce_stationarity=False,
                        enforce_invertibility=False,
                    ).fit(disp=False, maxiter=25)
                one_step_actual, one_step_pred = [], []
                two_step_actual, two_step_pred = [], []
                # Each test row is a forecast origin. The second forecast
                # step targets the next-next observation (60 minutes).
                for index, actual_value in enumerate(test.to_numpy(dtype=float)):
                    steps = 2 if index + 1 < len(test) else 1
                    forecast_values = np.asarray(
                        fitted.get_forecast(steps=steps).predicted_mean, dtype=float
                    )
                    one_step_actual.append(float(actual_value))
                    one_step_pred.append(float(forecast_values[0]))
                    if steps == 2:
                        two_step_actual.append(float(test.iloc[index + 1]))
                        two_step_pred.append(float(forecast_values[1]))
                    # Advance information state after scoring this origin;
                    # parameters stay fixed (refit=False).
                    fitted = fitted.extend(pd.Series([float(actual_value)], index=[test.index[index]]))
                per_horizon["30m"]["actual"].extend(one_step_actual)
                per_horizon["30m"]["predicted"].extend(one_step_pred)
                per_horizon["30m"]["locations"].append(location)
                per_horizon["60m"]["actual"].extend(two_step_actual)
                per_horizon["60m"]["predicted"].extend(two_step_pred)
                per_horizon["60m"]["locations"].append(location)
            except Exception as exc:
                report.setdefault("warnings", []).append(f"{location}/{target}: {type(exc).__name__}: {exc}")
        for horizon in ["30m", "60m"]:
            values = per_horizon[horizon]
            if not values["actual"]:
                report.setdefault("warnings", []).append(f"{horizon}/{target}: no valid forecast origins were produced")
                continue
            report["results"].setdefault(horizon, {})[target] = {
                **regression_metrics(values["actual"], values["predicted"]),
                "locations": values["locations"],
                "evaluation_type": "rolling-origin 1-step/2-step ahead; observed value appended after each origin without parameter refit",
            }
    out = ROOT / "outputs" / "forecasting"
    out.mkdir(parents=True, exist_ok=True)
    (out / "sarima_metrics.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-locations", type=int, default=None)
    parser.add_argument("--max-test-points", type=int, default=48,
                        help="Limit holdout origins per location for tractable SARIMA experiments")
    args = parser.parse_args()
    print(json.dumps(run(max_locations=args.max_locations, max_test_points=args.max_test_points), indent=2))
