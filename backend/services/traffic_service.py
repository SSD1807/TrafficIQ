"""Traffic data and Phase 1 model inference; sources are explicitly labelled."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from ml_pipeline import build_inference_row, normalize_bhubaneswar_data, ID_TO_LABEL


class TrafficService:
    def __init__(self, data_path: str | Path = "dataset/bhubaneswar_synthetic_traffic.csv",
                 model_path: str | Path = "models/best_model.pkl"):
        root = Path(__file__).resolve().parents[1]
        self.data_path = Path(data_path)
        if not self.data_path.is_absolute(): self.data_path = root / self.data_path
        self.model_path = Path(model_path)
        if not self.model_path.is_absolute(): self.model_path = root / self.model_path
        self.data = normalize_bhubaneswar_data(pd.read_csv(self.data_path)) if self.data_path.exists() else pd.DataFrame()
        self.model = joblib.load(self.model_path) if self.model_path.exists() else None
        self.known_locations = sorted(self.data["location"].astype(str).unique().tolist()) if not self.data.empty else []

    def list_locations(self) -> list[str]:
        return self.known_locations.copy()

    def history(self, location: str, periods: int = 12) -> pd.DataFrame:
        if self.data.empty:
            raise RuntimeError("Synthetic traffic dataset is unavailable")
        frame = self.data[self.data.location.astype(str) == str(location)].sort_values("timestamp").tail(max(1, periods))
        if frame.empty:
            raise ValueError(f"Unknown location: {location}")
        return frame.copy()

    def get_current_state(self) -> dict[str, Any]:
        if self.data.empty:
            return {"status": "unavailable", "source": "none", "is_live": False, "locations": []}
        latest_time = self.data.timestamp.max()
        rows = self.data[self.data.timestamp == latest_time]
        return {
            "status": "ok", "source": "synthetic_bhubaneswar_dataset", "is_live": False,
            "data_timestamp": latest_time.isoformat(),
            "notice": "This is the latest timestamp in the bundled synthetic dataset, not current live road traffic.",
            "locations": rows[["location", "vehicle_count", "avg_speed", "rainfall", "clouds", "temperature", "congestion_label"]].to_dict("records"),
        }

    def predict_next(self, location: str, prediction_timestamp: str | None = None,
                     overrides: dict[str, Any] | None = None,
                     weather_source: str | None = None) -> dict[str, Any]:
        if self.model is None:
            raise RuntimeError("Trained congestion model not found; run phase1_preprocess.py and phase2_train.py")
        hist = self.history(location, periods=1).iloc[-1]
        overrides = overrides or {}
        origin_time = pd.Timestamp(hist.timestamp)
        prediction_time = pd.Timestamp(prediction_timestamp) if prediction_timestamp else origin_time + timedelta(minutes=30)
        if prediction_time - origin_time != pd.Timedelta(minutes=30):
            raise ValueError("Phase 1 classifier expects the next consecutive 30-minute interval")
        row = build_inference_row(
            location=str(location), prediction_hour=prediction_time.hour,
            prediction_day_of_week=prediction_time.dayofweek,
            lag_vehicle_count=overrides.get("vehicle_count", hist.vehicle_count),
            lag_avg_speed=overrides.get("avg_speed", hist.avg_speed),
            lag_rainfall=overrides.get("rainfall", hist.rainfall),
            lag_clouds=overrides.get("clouds", hist.clouds),
            lag_temperature=overrides.get("temperature", hist.temperature),
        )
        pred = int(self.model.predict(row)[0])
        proba = self.model.predict_proba(row)[0].tolist() if hasattr(self.model, "predict_proba") else None
        return {
            "location": str(location), "origin_timestamp": origin_time.isoformat(),
            "prediction_timestamp": prediction_time.isoformat(),
            "prediction_horizon_minutes": 30,
            "predicted_class_id": pred, "predicted_congestion": ID_TO_LABEL[pred],
            "class_probabilities": {ID_TO_LABEL[i]: float(proba[i]) for i in range(len(proba))} if proba else None,
            "input_measurements": {
                "lag_vehicle_count": float(row.iloc[0]["lag_vehicle_count"]),
                "lag_avg_speed": float(row.iloc[0]["lag_avg_speed"]),
                "lag_rainfall": float(row.iloc[0]["lag_rainfall"]) if pd.notna(row.iloc[0]["lag_rainfall"]) else None,
                "lag_clouds": float(row.iloc[0]["lag_clouds"]) if pd.notna(row.iloc[0]["lag_clouds"]) else None,
                "lag_temperature": float(row.iloc[0]["lag_temperature"]) if pd.notna(row.iloc[0]["lag_temperature"]) else None,
            },
            "model_name": {
                "RandomForestClassifier": "Random Forest",
                "XGBClassifier": "XGBoost",
            }.get(
                self.model.named_steps.get("classifier").__class__.__name__,
                self.model.named_steps.get("classifier").__class__.__name__,
            ),
            "source": "synthetic_bhubaneswar_dataset", "is_live": False,
            "weather_input_source": weather_source or "synthetic_historical_weather",
            "notice": ("Prediction is generated from synthetic source history; it is not a live traffic observation. "
                       "If Open-Meteo weather was supplied, the model still has not been validated on real-world traffic."),
        }

    def predict_all(self) -> list[dict[str, Any]]:
        results = []
        for location in self.known_locations:
            try:
                results.append(self.predict_next(location))
            except Exception as exc:
                results.append({"location": location, "status": "error", "error": str(exc), "is_live": False})
        return results
