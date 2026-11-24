import numpy as np
import pandas as pd

from ml_pipeline import (
    FEATURE_COLUMNS, TARGET_COLUMN, build_supervised_dataset,
    chronological_split, create_model_pipeline, build_inference_row,
)


def small_source():
    rows = []
    times = pd.date_range("2024-01-01", periods=30, freq="30min")
    for loc_idx, location in enumerate(["A", "B"]):
        for i, ts in enumerate(times):
            rows.append({
                "timestamp": ts,
                "location": location,
                "vehicle_count": 100 + loc_idx * 20 + i,
                "avg_speed": 40 - i * 0.3,
                "rainfall": float(i % 3),
                "clouds": float(20 + i),
                "temperature": 25.0 + i * 0.1,
                "congestion_label": ["Low", "Medium", "High"][(i // 5 + loc_idx) % 3],
            })
    return pd.DataFrame(rows)


def test_target_is_next_interval_and_measurements_are_lagged():
    src = small_source()
    sup = build_supervised_dataset(src)
    row = sup[(sup.location == "A") & (sup.timestamp == pd.Timestamp("2024-01-01 00:00:00"))].iloc[0]
    assert row.prediction_timestamp == pd.Timestamp("2024-01-01 00:30:00")
    assert row.lag_vehicle_count == 100
    assert row[TARGET_COLUMN] == 0  # second interval retains the Low label
    assert "vehicle_count" not in FEATURE_COLUMNS
    assert "avg_speed" not in FEATURE_COLUMNS
    assert "congestion_label" not in FEATURE_COLUMNS


def test_gap_does_not_create_false_30m_target():
    src = small_source()
    src = src[~((src.location == "A") & (src.timestamp == pd.Timestamp("2024-01-01 00:30:00")))]
    sup = build_supervised_dataset(src)
    assert not ((sup.location == "A") & (sup.timestamp == pd.Timestamp("2024-01-01 00:00:00"))).any()


def test_chronological_split_has_no_timestamp_overlap():
    sup = build_supervised_dataset(small_source())
    split = chronological_split(sup)
    train_times = set(split.train.prediction_timestamp)
    val_times = set(split.validation.prediction_timestamp)
    test_times = set(split.test.prediction_timestamp)
    assert train_times.isdisjoint(val_times)
    assert train_times.isdisjoint(test_times)
    assert val_times.isdisjoint(test_times)
    assert max(train_times) < min(val_times) < max(val_times) < min(test_times)


def test_pipeline_handles_unseen_location_and_predicts_three_classes():
    sup = build_supervised_dataset(small_source())
    model = create_model_pipeline("Random Forest", random_state=7)
    model.fit(sup[FEATURE_COLUMNS], sup[TARGET_COLUMN])
    row = sup[FEATURE_COLUMNS].iloc[[0]].copy()
    row.loc[:, "location"] = "Unseen Location"
    prediction = model.predict(row)
    assert prediction.shape == (1,)
    assert prediction[0] in {0, 1, 2}


def test_inference_row_matches_training_feature_schema():
    row = build_inference_row(
        location="City Center", prediction_hour=8, prediction_day_of_week=1,
        lag_vehicle_count=550, lag_avg_speed=22.5, lag_rainfall=0.4,
        lag_clouds=70, lag_temperature=29.0,
    )
    assert row.columns.tolist() == FEATURE_COLUMNS
    assert row.loc[0, "prediction_is_rush_hour"] == 1
    assert row.loc[0, "lag_vehicle_count"] == 550
    assert "vehicle_count" not in row.columns


def test_current_label_is_present_only_for_persistence_comparison():
    sup = build_supervised_dataset(small_source())
    row = sup[(sup.location == "A") & (sup.timestamp == pd.Timestamp("2024-01-01 00:00:00"))].iloc[0]
    assert row.current_congestion_label == "Low"
    assert "current_congestion_label" not in FEATURE_COLUMNS
    assert "target_label_name" not in FEATURE_COLUMNS


def test_unknown_location_and_missing_numeric_inputs_are_handled():
    sup = build_supervised_dataset(small_source())
    train = sup[FEATURE_COLUMNS].copy()
    train.loc[train.index[:3], "lag_rainfall"] = np.nan
    model = create_model_pipeline("Random Forest", random_state=9)
    model.fit(train, sup[TARGET_COLUMN])
    row = build_inference_row("New Road", 13, 2, np.nan, np.nan, np.nan, np.nan, np.nan)
    pred = model.predict(row)
    assert pred.shape == (1,)
    assert pred[0] in {0, 1, 2}
