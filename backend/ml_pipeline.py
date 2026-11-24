"""Leakage-aware ML utilities for 30-minute-ahead Bhubaneswar congestion prediction.

The target is the congestion label observed at the next 30-minute interval for
one location. Only measurements from the preceding interval and calendar
features known at prediction time are included in X.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier

RANDOM_STATE = 42
INTERVAL = pd.Timedelta(minutes=30)
LABEL_TO_ID = {"Low": 0, "Medium": 1, "High": 2}
ID_TO_LABEL = {v: k for k, v in LABEL_TO_ID.items()}

CATEGORICAL_FEATURES = ["location"]
NUMERIC_FEATURES = [
    "prediction_hour",
    "prediction_day_of_week",
    "prediction_is_weekend",
    "prediction_is_rush_hour",
    "prediction_is_night",
    "prediction_hour_sin",
    "prediction_hour_cos",
    "prediction_dow_sin",
    "prediction_dow_cos",
    "lag_vehicle_count",
    "lag_avg_speed",
    "lag_rainfall",
    "lag_clouds",
    "lag_temperature",
]
FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES
TARGET_COLUMN = "target_congestion_label"

REQUIRED_COLUMNS = [
    "timestamp", "location", "vehicle_count", "avg_speed",
    "rainfall", "clouds", "temperature", "congestion_label",
]


def _parse_timestamps(values: pd.Series) -> pd.Series:
    """Parse ISO and common day-first legacy timestamp strings safely."""
    parsed = pd.to_datetime(values, errors="coerce", format="mixed", dayfirst=False)
    bad = parsed.isna()
    if bad.any():
        parsed.loc[bad] = pd.to_datetime(
            values.loc[bad], errors="coerce", dayfirst=True
        )
    return parsed


def normalize_bhubaneswar_data(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize a Bhubaneswar source file without mixing other domains."""
    df = raw.copy()
    rename = {
        "date_time": "timestamp",
        "rain_1h": "rainfall",
        "clouds_all": "clouds",
        "temp_celsius": "temperature",
        "congestion_name": "congestion_label",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns and v not in df.columns})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Bhubaneswar dataset is missing required columns: {missing}")

    df = df[REQUIRED_COLUMNS].copy()
    df["timestamp"] = _parse_timestamps(df["timestamp"])
    df["location"] = df["location"].astype("string").str.strip()
    for col in ["vehicle_count", "avg_speed", "rainfall", "clouds", "temperature"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Canonicalize labels; numeric labels are accepted for legacy files.
    label_map = {"low": "Low", "medium": "Medium", "high": "High"}
    labels = df["congestion_label"].astype("string").str.strip()
    labels = labels.str.lower().map(label_map)
    numeric_labels = pd.to_numeric(df["congestion_label"], errors="coerce")
    labels = labels.fillna(numeric_labels.map(ID_TO_LABEL))
    df["congestion_label"] = labels

    # Missing exogenous measurements are imputed downstream using training data.
    df = df.dropna(subset=["timestamp", "location", "congestion_label"])
    df = df[df["location"].str.len() > 0]
    df = df[df["congestion_label"].isin(LABEL_TO_ID)]
    df = df.sort_values(["location", "timestamp"]).drop_duplicates(
        ["location", "timestamp"], keep="last"
    )
    return df.reset_index(drop=True)


def _calendar_features(hour, dow) -> dict:
    """Single source of truth for calendar feature engineering."""
    hour = np.asarray(hour, dtype=int)
    dow = np.asarray(dow, dtype=int)
    hour_rad = 2.0 * np.pi * hour / 24.0
    dow_rad = 2.0 * np.pi * dow / 7.0
    return {
        "prediction_hour": hour,
        "prediction_day_of_week": dow,
        "prediction_is_weekend": (dow >= 5).astype(int),
        "prediction_is_rush_hour": (
            ((hour >= 7) & (hour <= 10)) | ((hour >= 17) & (hour <= 20))
        ).astype(int),
        "prediction_is_night": ((hour >= 22) | (hour <= 5)).astype(int),
        "prediction_hour_sin": np.sin(hour_rad),
        "prediction_hour_cos": np.cos(hour_rad),
        "prediction_dow_sin": np.sin(dow_rad),
        "prediction_dow_cos": np.cos(dow_rad),
    }


def _time_features(ts: pd.Series) -> pd.DataFrame:
    hour = ts.dt.hour.fillna(0).astype(int).to_numpy()
    dow = ts.dt.dayofweek.fillna(0).astype(int).to_numpy()
    return pd.DataFrame(_calendar_features(hour, dow), index=ts.index)


def build_inference_row(
    location: str,
    prediction_hour: int,
    prediction_day_of_week: int,
    lag_vehicle_count: float,
    lag_avg_speed: float,
    lag_rainfall: float,
    lag_clouds: float,
    lag_temperature: float,
) -> pd.DataFrame:
    """Create a one-row inference frame using the exact training feature schema."""
    calendar = _calendar_features([prediction_hour], [prediction_day_of_week])
    values = {
        "location": [str(location)],
        **{key: value for key, value in calendar.items()},
        "lag_vehicle_count": [lag_vehicle_count],
        "lag_avg_speed": [lag_avg_speed],
        "lag_rainfall": [lag_rainfall],
        "lag_clouds": [lag_clouds],
        "lag_temperature": [lag_temperature],
    }
    return pd.DataFrame(values, columns=FEATURE_COLUMNS)


def build_supervised_dataset(raw: pd.DataFrame) -> pd.DataFrame:
    """Create one row per location/time predicting the *next* 30-minute label.

    Sensor/weather columns are shifted by one interval. The target comes from
    the next observation. Gaps other than exactly 30 minutes are dropped so a
    missing interval cannot silently become a longer-horizon target.
    """
    df = normalize_bhubaneswar_data(raw)
    frames = []
    for location, group in df.groupby("location", sort=True):
        group = group.sort_values("timestamp").reset_index(drop=True)
        next_timestamp = group["timestamp"].shift(-1)
        is_next_interval = (next_timestamp - group["timestamp"]) == INTERVAL
        target = group["congestion_label"].shift(-1)

        part = pd.DataFrame(index=group.index)
        part["timestamp"] = group["timestamp"]
        part["prediction_timestamp"] = next_timestamp
        part["location"] = location
        part["lag_vehicle_count"] = group["vehicle_count"]
        part["lag_avg_speed"] = group["avg_speed"]
        part["lag_rainfall"] = group["rainfall"]
        part["lag_clouds"] = group["clouds"]
        part["lag_temperature"] = group["temperature"]
        # Persistence comparator only: this is the observed class at the
        # prediction origin (time t), not an input to the learned classifiers.
        part["current_congestion_label"] = group["congestion_label"]
        part[TARGET_COLUMN] = target.map(LABEL_TO_ID)
        part["target_label_name"] = target

        time_df = _time_features(next_timestamp)
        for col in time_df.columns:
            part[col] = time_df[col]

        part = part.loc[is_next_interval].dropna(subset=[TARGET_COLUMN])
        frames.append(part)

    if not frames:
        raise ValueError("No consecutive 30-minute observations were found.")
    result = pd.concat(frames, ignore_index=True)
    result[TARGET_COLUMN] = result[TARGET_COLUMN].astype(int)
    result = result.sort_values(["prediction_timestamp", "location"]).reset_index(drop=True)
    if result.empty:
        raise ValueError("The supervised dataset is empty after interval alignment.")
    return result[[
        "timestamp", "prediction_timestamp", *FEATURE_COLUMNS,
        "current_congestion_label", TARGET_COLUMN, "target_label_name",
    ]]


@dataclass
class TemporalSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    train_end: str
    validation_end: str


def chronological_split(
    data: pd.DataFrame, train_fraction: float = 0.70, validation_fraction: float = 0.15
) -> TemporalSplit:
    """Split by unique prediction timestamps, keeping all locations together."""
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("Split fractions must be between 0 and 1.")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("Train + validation fractions must be less than 1.")
    unique_times = np.array(sorted(pd.to_datetime(data["prediction_timestamp"]).unique()))
    if len(unique_times) < 10:
        raise ValueError("At least 10 unique prediction timestamps are required.")

    n_train = max(1, int(len(unique_times) * train_fraction))
    n_val = max(1, int(len(unique_times) * validation_fraction))
    if n_train + n_val >= len(unique_times):
        n_val = len(unique_times) - n_train - 1
    train_times = set(unique_times[:n_train])
    val_times = set(unique_times[n_train:n_train + n_val])
    test_times = set(unique_times[n_train + n_val:])

    timestamps = pd.to_datetime(data["prediction_timestamp"])
    train = data.loc[timestamps.isin(train_times)].copy()
    validation = data.loc[timestamps.isin(val_times)].copy()
    test = data.loc[timestamps.isin(test_times)].copy()
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("Temporal split produced an empty partition.")
    return TemporalSplit(
        train=train.reset_index(drop=True),
        validation=validation.reset_index(drop=True),
        test=test.reset_index(drop=True),
        train_end=str(pd.Timestamp(max(train_times))),
        validation_end=str(pd.Timestamp(max(val_times))),
    )


def create_model_pipeline(model_name: str, random_state: int = RANDOM_STATE) -> Pipeline:
    """Create RF or XGBoost pipeline with train-fitted imputation/encoding."""
    name = model_name.strip().lower()
    preprocessing = ColumnTransformer(
        transformers=[
            ("categorical", Pipeline([
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("onehot", OneHotEncoder(handle_unknown="ignore")),
            ]), CATEGORICAL_FEATURES),
            ("numeric", Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
            ]), NUMERIC_FEATURES),
        ],
        remainder="drop",
    )

    if name in {"random forest", "random_forest", "rf"}:
        estimator = RandomForestClassifier(
            n_estimators=350,
            max_depth=18,
            min_samples_leaf=2,
            class_weight="balanced_subsample",
            random_state=random_state,
            n_jobs=-1,
        )
    elif name in {"xgboost", "xgb"}:
        estimator = XGBClassifier(
            n_estimators=350,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            min_child_weight=2,
            reg_lambda=1.0,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            tree_method="hist",
            random_state=random_state,
            n_jobs=-1,
            verbosity=0,
        )
    else:
        raise ValueError(f"Unknown model_name: {model_name}")
    return Pipeline([("preprocess", preprocessing), ("classifier", estimator)])


def balanced_sample_weights(y: pd.Series | np.ndarray) -> np.ndarray:
    """Inverse-frequency sample weights for imbalanced XGBoost training."""
    labels = np.asarray(y, dtype=int)
    counts = np.bincount(labels, minlength=3)
    present = counts > 0
    weights = np.ones_like(labels, dtype=float)
    if present.any():
        class_weights = np.zeros(3, dtype=float)
        class_weights[present] = len(labels) / (present.sum() * counts[present])
        weights = class_weights[labels]
    return weights
