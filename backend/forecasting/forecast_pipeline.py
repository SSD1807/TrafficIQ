"""Direct 30/60-minute traffic-volume and speed forecasting.

This module is intentionally separate from ml_pipeline.py's next-interval
congestion classifier. It uses only observations available at each forecast
origin and time/location context known in advance.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import joblib
import sklearn
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, accuracy_score, precision_score, recall_score, f1_score, classification_report, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

try:
    from xgboost import XGBRegressor
except Exception:  # optional
    XGBRegressor = None

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dataset" / "bhubaneswar_synthetic_traffic.csv"
MODEL_DIR = ROOT / "models" / "forecasting"
OUTPUT_DIR = ROOT / "outputs" / "forecasting"
HORIZONS = {30: 1, 60: 2}
TARGETS = ["vehicle_count", "avg_speed"]


def _calendar_features(ts: pd.Series) -> pd.DataFrame:
    hour = ts.dt.hour.to_numpy()
    dow = ts.dt.dayofweek.to_numpy()
    h = 2 * np.pi * hour / 24
    d = 2 * np.pi * dow / 7
    return pd.DataFrame({
        "forecast_hour": hour,
        "forecast_day_of_week": dow,
        "forecast_is_weekend": (dow >= 5).astype(int),
        "forecast_is_rush_hour": (((hour >= 7) & (hour <= 10)) | ((hour >= 17) & (hour <= 20))).astype(int),
        "forecast_hour_sin": np.sin(h), "forecast_hour_cos": np.cos(h),
        "forecast_dow_sin": np.sin(d), "forecast_dow_cos": np.cos(d),
    }, index=ts.index)


def build_forecast_frame(raw: pd.DataFrame, horizon_steps: int) -> tuple[pd.DataFrame, list[str]]:
    if horizon_steps not in (1, 2):
        raise ValueError("Only direct 30-minute and 60-minute horizons (1 or 2 steps) are supported")
    from ml_pipeline import normalize_bhubaneswar_data
    df = normalize_bhubaneswar_data(raw)
    frames = []
    feature_cols = ["location", "forecast_hour", "forecast_day_of_week", "forecast_is_weekend",
                    "forecast_is_rush_hour", "forecast_hour_sin", "forecast_hour_cos",
                    "forecast_dow_sin", "forecast_dow_cos"]
    for lag in range(4):
        feature_cols += [f"lag_{name}_{lag}" for name in TARGETS + ["rainfall", "temperature", "clouds"]]
    feature_cols += [f"rolling_{name}_{window}" for name in TARGETS for window in (3, 6)]
    for location, grp in df.groupby("location", sort=True):
        grp = grp.sort_values("timestamp").reset_index(drop=True)
        times = grp["timestamp"]
        part = pd.DataFrame({"timestamp": times, "target_timestamp": times.shift(-horizon_steps), "location": location})
        cal = _calendar_features(pd.to_datetime(part["target_timestamp"]))
        part = pd.concat([part, cal], axis=1)
        for lag in range(4):
            for name in TARGETS + ["rainfall", "temperature", "clouds"]:
                part[f"lag_{name}_{lag}"] = grp[name].shift(lag)
        for name in TARGETS:
            for window in (3, 6):
                part[f"rolling_{name}_{window}"] = grp[name].rolling(window, min_periods=1).mean()
        for target in TARGETS:
            part[f"target_{target}"] = grp[target].shift(-horizon_steps)
        part["target_congestion_label"] = grp["congestion_label"].shift(-horizon_steps)
        part = part.dropna(subset=["target_timestamp", *[f"target_{t}" for t in TARGETS]])
        frames.append(part)
    out = pd.concat(frames, ignore_index=True).sort_values(["target_timestamp", "location"]).reset_index(drop=True)
    return out, feature_cols


def _split_by_target_time(frame: pd.DataFrame, fraction: float = 0.80):
    times = np.array(sorted(pd.to_datetime(frame["target_timestamp"]).unique()))
    cut = max(1, min(len(times) - 1, int(len(times) * fraction)))
    train_times, test_times = set(times[:cut]), set(times[cut:])
    train = frame[pd.to_datetime(frame.target_timestamp).isin(train_times)].copy()
    test = frame[pd.to_datetime(frame.target_timestamp).isin(test_times)].copy()
    if train.empty or test.empty:
        raise ValueError("Forecast train/test split is empty")
    return train, test


def smape(y_true, y_pred) -> float:
    a, b = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    denom = np.abs(a) + np.abs(b)
    return float(np.mean(2 * np.abs(a - b) / np.maximum(denom, 1e-6)) * 100)


def regression_metrics(y_true, y_pred) -> dict:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(math.sqrt(mean_squared_error(y_true, y_pred))),
        "smape_pct": smape(y_true, y_pred),
        "n": int(len(y_true)),
    }


def _make_regressor(name: str, random_state: int = 42) -> Pipeline:
    numeric = [f for f in _FEATURE_COLS if f != "location"]
    prep = ColumnTransformer([
        ("loc", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                           ("onehot", OneHotEncoder(handle_unknown="ignore"))]), ["location"]),
        ("num", SimpleImputer(strategy="median"), numeric),
    ])
    if name == "Random Forest":
        est = RandomForestRegressor(n_estimators=45, max_depth=10, min_samples_leaf=6,
                                    max_features=0.8, random_state=random_state, n_jobs=-1)
    elif name == "XGBoost":
        if XGBRegressor is None:
            raise RuntimeError("XGBoost is not installed")
        est = XGBRegressor(n_estimators=90, max_depth=3, learning_rate=0.05,
                           subsample=0.85, colsample_bytree=0.85, objective="reg:squarederror",
                           reg_lambda=1.0, random_state=random_state, n_jobs=-1, verbosity=0)
    else:
        raise ValueError(name)
    return Pipeline([("preprocess", prep), ("regressor", est)])


def _make_classifier(random_state: int = 42) -> Pipeline:
    numeric = [f for f in _FEATURE_COLS if f != "location"]
    prep = ColumnTransformer([
        ("loc", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                           ("onehot", OneHotEncoder(handle_unknown="ignore"))]), ["location"]),
        ("num", SimpleImputer(strategy="median"), numeric),
    ])
    est = RandomForestClassifier(n_estimators=45, max_depth=10, min_samples_leaf=6,
                                 max_features=0.8, class_weight="balanced_subsample", random_state=random_state, n_jobs=-1)
    return Pipeline([("preprocess", prep), ("classifier", est)])


# Set inside benchmark per horizon before constructing an estimator.
_FEATURE_COLS: list[str] = []


def derive_congestion_label(vehicle_count: float, avg_speed: float) -> str:
    """Mirror the documented synthetic generator rule for a derived forecast class."""
    if (vehicle_count > 320 and avg_speed < 18) or (vehicle_count > 300 and avg_speed < 20):
        return "High"
    if (vehicle_count > 230 and avg_speed < 30) or (vehicle_count > 200 and avg_speed < 35) or vehicle_count > 280:
        return "Medium"
    return "Low"


def benchmark(*, random_state: int = 42) -> dict:
    global _FEATURE_COLS
    raw = pd.read_csv(SOURCE)
    report = {"metadata": {
        "dataset": str(SOURCE.relative_to(ROOT)),
        "domain": "synthetic Bhubaneswar traffic; not real-world observed measurements",
        "split": "chronological 80/20 by target_timestamp; identical holdout within each horizon",
        "horizons_minutes": list(HORIZONS),
        "targets": TARGETS,
        "models": ["Persistence", "Moving Average", "Random Forest"] + (["XGBoost"] if XGBRegressor else []),
        "runtime_versions": {"python": __import__("sys").version.split()[0], "pandas": pd.__version__, "numpy": np.__version__,
                             "scikit_learn": sklearn.__version__, "xgboost": __import__("xgboost").__version__, "joblib": joblib.__version__},
        "sarima_status": "separate optional rolling-origin experiment; run forecasting/sarima_runner.py",
        "lstm_status": "not trained: TensorFlow is optional and the available panel is synthetic; no independent real-world series to justify a deep sequence model",
    }, "results": {}}
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for minutes, steps in HORIZONS.items():
        frame, features = build_forecast_frame(raw, steps)
        _FEATURE_COLS = features
        train, test = _split_by_target_time(frame)
        X_train, X_test = train[features], test[features]
        results = {"split": {
            "train_rows": int(len(train)), "test_rows": int(len(test)),
            "train_target_end": str(train.target_timestamp.max()),
            "test_target_start": str(test.target_timestamp.min()),
            "test_target_end": str(test.target_timestamp.max()),
        }, "targets": {}}
        for target in TARGETS:
            y_train = train[f"target_{target}"].astype(float)
            y_test = test[f"target_{target}"].astype(float)
            # Baselines use only measurements available at the forecast origin.
            current_col = f"lag_{target}_0"
            ma_col = f"rolling_{target}_3"
            preds = {
                "Persistence": test[current_col].astype(float).fillna(y_train.median()).to_numpy(),
                "Moving Average": test[ma_col].astype(float).fillna(y_train.median()).to_numpy(),
            }
            target_report = {name: regression_metrics(y_test, pred) for name, pred in preds.items()}
            for name in ["Random Forest"] + (["XGBoost"] if XGBRegressor else []):
                model = _make_regressor(name, random_state)
                model.fit(X_train, y_train)
                pred = model.predict(X_test)
                preds[name] = pred
                target_report[name] = regression_metrics(y_test, pred)
                joblib.dump({"model": model, "feature_columns": features, "target": target,
                             "horizon_minutes": minutes, "metadata": report["metadata"]},
                            MODEL_DIR / f"{name.lower().replace(' ', '_')}_{minutes}m_{target}.joblib")
            results["targets"][target] = target_report
            fig, ax = plt.subplots(figsize=(11, 4))
            plot_n = min(240, len(y_test))
            ax.plot(np.arange(plot_n), y_test.to_numpy()[:plot_n], label="Actual", linewidth=1.8)
            for model_label, values in preds.items():
                ax.plot(np.arange(plot_n), np.asarray(values)[:plot_n], label=model_label, alpha=0.8)
            ax.set_title(f"{minutes}-minute {target.replace('_', ' ')} forecast — first {plot_n} test samples")
            ax.set_xlabel("Chronological test sample")
            ax.set_ylabel(target.replace("_", " "))
            ax.legend(ncol=3)
            fig.tight_layout()
            fig.savefig(OUTPUT_DIR / f"forecast_comparison_{minutes}m_{target}.png", dpi=130)
            plt.close(fig)

        # Separate congestion-class forecaster for the same horizon. This is
        # not inferred from same-interval target measurements; it learns from
        # lagged features and future calendar features only.
        class_map = {"Low": 0, "Medium": 1, "High": 2}
        y_cls_train = train["target_congestion_label"].astype(str).map(class_map)
        y_cls_test = test["target_congestion_label"].astype(str).map(class_map)
        valid_train = y_cls_train.notna()
        valid_test = y_cls_test.notna()
        class_model = _make_classifier(random_state)
        class_model.fit(X_train.loc[valid_train], y_cls_train.loc[valid_train].astype(int))
        cls_pred = class_model.predict(X_test.loc[valid_test]).astype(int)
        cm = confusion_matrix(y_cls_test.loc[valid_test].astype(int), cls_pred, labels=[0, 1, 2])
        results["congestion_classification"] = {
            "model": "Random Forest classifier",
            "accuracy": float(accuracy_score(y_cls_test.loc[valid_test].astype(int), cls_pred)),
            "precision_macro": float(precision_score(y_cls_test.loc[valid_test].astype(int), cls_pred, average="macro", zero_division=0)),
            "recall_macro": float(recall_score(y_cls_test.loc[valid_test].astype(int), cls_pred, average="macro", zero_division=0)),
            "f1_macro": float(f1_score(y_cls_test.loc[valid_test].astype(int), cls_pred, average="macro", zero_division=0)),
            "classification_report": classification_report(y_cls_test.loc[valid_test].astype(int), cls_pred,
                labels=[0, 1, 2], target_names=["Low", "Medium", "High"], output_dict=True, zero_division=0),
            "confusion_matrix": cm.tolist(),
            "test_rows": int(valid_test.sum()),
            "label_mapping": class_map,
        }
        joblib.dump({"model": class_model, "feature_columns": features, "horizon_minutes": minutes,
                     "classes": class_map}, MODEL_DIR / f"random_forest_classifier_{minutes}m.joblib")
        fig, ax = plt.subplots(figsize=(5, 4))
        sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", xticklabels=["Low", "Medium", "High"],
                    yticklabels=["Low", "Medium", "High"], ax=ax)
        ax.set_xlabel("Predicted"); ax.set_ylabel("Actual"); ax.set_title(f"{minutes}-minute congestion forecast classifier")
        fig.tight_layout(); fig.savefig(OUTPUT_DIR / f"confusion_matrix_classification_{minutes}m.png", dpi=130); plt.close(fig)
        report["results"][f"{minutes}m"] = results
    (OUTPUT_DIR / "forecast_metrics.json").write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()
    result = benchmark(random_state=args.random_state)
    print(json.dumps(result, indent=2))
