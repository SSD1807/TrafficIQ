"""Runtime forecast inference using trained 30/60-minute forecasting artifacts."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from forecasting.forecast_pipeline import _calendar_features, derive_congestion_label, TARGETS


class ForecastService:
    def __init__(self, data_path: str | Path = "dataset/bhubaneswar_synthetic_traffic.csv",
                 model_dir: str | Path = "models/forecasting"):
        root = Path(__file__).resolve().parents[1]
        self.data_path = Path(data_path); self.model_dir = Path(model_dir)
        if not self.data_path.is_absolute(): self.data_path = root / self.data_path
        if not self.model_dir.is_absolute(): self.model_dir = root / self.model_dir
        self.data = pd.read_csv(self.data_path, parse_dates=["timestamp"]) if self.data_path.exists() else pd.DataFrame()

    def _feature_row(self, location: str, horizon_minutes: int) -> pd.DataFrame:
        if horizon_minutes not in (30, 60):
            raise ValueError("horizon_minutes must be 30 or 60")
        if self.data.empty: raise RuntimeError("Forecast source dataset is unavailable")
        hist = self.data[self.data.location.astype(str) == str(location)].sort_values("timestamp").tail(6).reset_index(drop=True)
        if hist.empty: raise ValueError(f"Unknown location: {location}")
        origin = pd.Timestamp(hist.iloc[-1].timestamp)
        target_time = origin + timedelta(minutes=horizon_minutes)
        cal = _calendar_features(pd.Series([target_time]))
        row: dict[str, Any] = {"location": str(location), **cal.iloc[0].to_dict()}
        for lag in range(4):
            record = hist.iloc[-1 - lag] if len(hist) > lag else None
            for name in TARGETS + ["rainfall", "temperature", "clouds"]:
                row[f"lag_{name}_{lag}"] = record[name] if record is not None else np.nan
        for name in TARGETS:
            for window in (3, 6):
                row[f"rolling_{name}_{window}"] = hist[name].tail(window).mean()
        return pd.DataFrame([row]), origin, target_time

    REGRESSOR_MODELS = {"random_forest": "Random Forest", "xgboost": "XGBoost"}

    def available_regressors(self, horizon_minutes: int) -> list[str]:
        return [m for m in self.REGRESSOR_MODELS
                if all((self.model_dir / f"{m}_{horizon_minutes}m_{t}.joblib").exists() for t in TARGETS)]

    def forecast(self, location: str, horizon_minutes: int, model: str = "random_forest") -> dict[str, Any]:
        if model not in self.REGRESSOR_MODELS:
            raise ValueError(f"model must be one of {sorted(self.REGRESSOR_MODELS)}")
        features, origin, target_time = self._feature_row(location, horizon_minutes)
        result: dict[str, Any] = {
            "location": location, "origin_timestamp": origin.isoformat(),
            "target_timestamp": target_time.isoformat(), "horizon_minutes": horizon_minutes,
            "source": "synthetic_bhubaneswar_dataset", "is_live": False,
            "notice": "Forecast uses the last bundled synthetic historical observation; it is not a real-time traffic forecast.",
        }
        for target in TARGETS:
            path = self.model_dir / f"{model}_{horizon_minutes}m_{target}.joblib"
            if not path.exists():
                result[target] = None
                continue
            bundle = joblib.load(path)
            pred = float(bundle["model"].predict(features[bundle["feature_columns"]])[0])
            result[target] = max(0.0, pred) if target == "vehicle_count" else max(0.0, pred)
        classifier_path = self.model_dir / f"random_forest_classifier_{horizon_minutes}m.joblib"
        if classifier_path.exists():
            bundle = joblib.load(classifier_path)
            pred_id = int(bundle["model"].predict(features[bundle["feature_columns"]])[0])
            reverse = {v: k for k, v in bundle["classes"].items()}
            proba = bundle["model"].predict_proba(features[bundle["feature_columns"]])[0]
            result["predicted_congestion"] = reverse[pred_id]
            result["congestion_probabilities"] = {reverse[i]: float(proba[i]) for i in range(len(proba))}
        elif result.get("vehicle_count") is not None and result.get("avg_speed") is not None:
            result["predicted_congestion"] = derive_congestion_label(result["vehicle_count"], result["avg_speed"])
            result["congestion_probabilities"] = None
        else:
            result["predicted_congestion"] = None
            result["congestion_probabilities"] = None
        result["forecast_model"] = f"{self.REGRESSOR_MODELS[model]} regressors + Random Forest classifier"
        result["regressor_model"] = model
        return result
