"""Train/evaluate the 30-minute-ahead congestion classifiers and baselines.

Run after phase1_preprocess.py:
    python phase2_train.py

Evaluation uses one chronological, timestamp-grouped holdout for every model:
Random Forest, XGBoost, and a persistence baseline (current label -> next label).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import sklearn
import xgboost
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    confusion_matrix, classification_report,
)
from sklearn.preprocessing import LabelEncoder

from ml_pipeline import (
    FEATURE_COLUMNS, TARGET_COLUMN, ID_TO_LABEL, LABEL_TO_ID,
    balanced_sample_weights, chronological_split, create_model_pipeline,
)

ROOT = Path(__file__).resolve().parent
LABEL_IDS = [0, 1, 2]
LABEL_NAMES = [ID_TO_LABEL[i] for i in LABEL_IDS]


def evaluate(y_true, y_pred) -> dict:
    cm = confusion_matrix(y_true, y_pred, labels=LABEL_IDS)
    report = classification_report(
        y_true, y_pred, labels=LABEL_IDS, target_names=LABEL_NAMES,
        output_dict=True, zero_division=0,
    )
    high_idx = LABEL_TO_ID["High"]
    high_tp = int(cm[high_idx, high_idx])
    high_fp = int(cm[:, high_idx].sum() - high_tp)
    high_fn = int(cm[high_idx, :].sum() - high_tp)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_macro": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "precision_weighted": float(precision_score(y_true, y_pred, average="weighted", zero_division=0)),
        "recall_macro": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
        "recall_weighted": float(recall_score(y_true, y_pred, average="weighted", zero_division=0)),
        "f1_macro": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "f1_weighted": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
        "classification_report": report,
        "confusion_matrix": cm.tolist(),
        "high_class_error_analysis": {
            "true_positives": high_tp,
            "false_positives": high_fp,
            "false_negatives": high_fn,
            "precision": float(high_tp / (high_tp + high_fp)) if high_tp + high_fp else 0.0,
            "recall": float(high_tp / (high_tp + high_fn)) if high_tp + high_fn else 0.0,
            "support": int(cm[high_idx, :].sum()),
        },
    }


def fit_model(name: str, frame: pd.DataFrame, random_state: int):
    model = create_model_pipeline(name, random_state=random_state)
    x = frame[FEATURE_COLUMNS]
    y = frame[TARGET_COLUMN].astype(int)
    if name == "XGBoost":
        model.fit(x, y, classifier__sample_weight=balanced_sample_weights(y))
    else:
        model.fit(x, y)
    return model


def save_confusion_matrix(cm, title: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", xticklabels=LABEL_NAMES,
                yticklabels=LABEL_NAMES, ax=ax)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def save_comparison(metrics: dict[str, dict], path: Path) -> None:
    names = list(metrics)
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(9, 5))
    width = 0.24
    for offset, metric, label in [
        (-width, "accuracy", "Accuracy"),
        (0, "f1_macro", "Macro F1"),
        (width, "f1_weighted", "Weighted F1"),
    ]:
        ax.bar(x + offset, [metrics[n][metric] for n in names], width, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=12, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Score")
    ax.set_title("30-minute congestion classification — identical chronological test window")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def current_label_ids(frame: pd.DataFrame) -> np.ndarray:
    if "current_congestion_label" not in frame:
        raise ValueError(
            "Processed data is missing current_congestion_label. "
            "Re-run phase1_preprocess.py before training."
        )
    values = frame["current_congestion_label"].astype(str).str.strip().str.title()
    unknown = sorted(set(values) - set(LABEL_TO_ID))
    if unknown:
        raise ValueError(f"Unknown current congestion labels for persistence baseline: {unknown}")
    return values.map(LABEL_TO_ID).to_numpy(dtype=int)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="dataset/processed_bhubaneswar_traffic.csv")
    parser.add_argument("--models-dir", default="models")
    parser.add_argument("--outputs-dir", default="outputs")
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute(): input_path = ROOT / input_path
    models_dir = Path(args.models_dir)
    if not models_dir.is_absolute(): models_dir = ROOT / models_dir
    outputs_dir = Path(args.outputs_dir)
    if not outputs_dir.is_absolute(): outputs_dir = ROOT / outputs_dir
    models_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/7] Loading supervised dataset: {input_path}")
    df = pd.read_csv(input_path, parse_dates=["timestamp", "prediction_timestamp"])
    required = set(FEATURE_COLUMNS + [TARGET_COLUMN, "prediction_timestamp", "current_congestion_label"])
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Processed dataset missing columns: {missing}. Re-run phase1_preprocess.py.")

    # Ensure every timestamp has exactly one partition and the interval alignment is valid.
    delta = pd.to_datetime(df["prediction_timestamp"]) - pd.to_datetime(df["timestamp"])
    if not delta.eq(pd.Timedelta(minutes=30)).all():
        raise ValueError("Found supervised examples that are not exactly 30 minutes apart.")
    split = chronological_split(df)
    partitions = {"train": split.train, "validation": split.validation, "test": split.test}
    print("[2/7] Chronological split by prediction timestamp:")
    for name, part in partitions.items():
        print(f"  {name:10s}: {len(part):>7,} rows | {part.prediction_timestamp.min()} -> {part.prediction_timestamp.max()} | {part[TARGET_COLUMN].value_counts().sort_index().to_dict()}")
    # No timestamp may appear in two partitions.
    time_sets = [set(pd.to_datetime(p.prediction_timestamp).unique()) for p in partitions.values()]
    if any(time_sets[i] & time_sets[j] for i in range(3) for j in range(i + 1, 3)):
        raise AssertionError("Chronological split leaked prediction timestamps across partitions.")

    y_val = split.validation[TARGET_COLUMN].astype(int)
    y_test = split.test[TARGET_COLUMN].astype(int)
    val_persistence = evaluate(y_val, current_label_ids(split.validation))
    test_persistence = evaluate(y_test, current_label_ids(split.test))

    print("[3/7] Fitting Random Forest and XGBoost on the training window...")
    validation_metrics = {"Persistence baseline": val_persistence}
    for model_name in ["Random Forest", "XGBoost"]:
        model = fit_model(model_name, split.train, args.random_state)
        val_pred = model.predict(split.validation[FEATURE_COLUMNS])
        validation_metrics[model_name] = evaluate(y_val, val_pred)
        print(f"  {model_name:18s} validation macro-F1={validation_metrics[model_name]['f1_macro']:.4f} | accuracy={validation_metrics[model_name]['accuracy']:.4f}")
    print(f"  {'Persistence':18s} validation macro-F1={val_persistence['f1_macro']:.4f} | accuracy={val_persistence['accuracy']:.4f}")

    selected_name = max(
        ["Random Forest", "XGBoost"],
        key=lambda key: validation_metrics[key]["f1_macro"],
    )
    print(f"[4/7] Selected by validation macro-F1 (persistence is a comparator only): {selected_name}")

    print("[5/7] Re-fitting both candidates on train + validation, then evaluating on identical holdout test...")
    train_val = pd.concat([split.train, split.validation], ignore_index=True)
    test_metrics = {"Persistence baseline": test_persistence}
    trained_models = {}
    predictions = {"Persistence baseline": current_label_ids(split.test)}
    for model_name in ["Random Forest", "XGBoost"]:
        model = fit_model(model_name, train_val, args.random_state)
        test_pred = model.predict(split.test[FEATURE_COLUMNS])
        metrics = evaluate(y_test, test_pred)
        metrics["validation_selection_macro_f1"] = validation_metrics[model_name]["f1_macro"]
        test_metrics[model_name] = metrics
        trained_models[model_name] = model
        predictions[model_name] = test_pred
        print(f"  {model_name:18s} TEST accuracy={metrics['accuracy']:.4f} | macro-F1={metrics['f1_macro']:.4f} | High P/R={metrics['high_class_error_analysis']['precision']:.4f}/{metrics['high_class_error_analysis']['recall']:.4f}")
    print(f"  {'Persistence':18s} TEST accuracy={test_persistence['accuracy']:.4f} | macro-F1={test_persistence['f1_macro']:.4f} | High P/R={test_persistence['high_class_error_analysis']['precision']:.4f}/{test_persistence['high_class_error_analysis']['recall']:.4f}")

    best_model = trained_models[selected_name]
    for model_label, metrics in test_metrics.items():
        safe_name = model_label.lower().replace(" ", "_")
        save_confusion_matrix(
            metrics["confusion_matrix"],
            f"Confusion Matrix — {model_label} (chronological test)",
            outputs_dir / f"confusion_matrix_{safe_name}.png",
        )
    # Retain legacy filename for existing dashboard references.
    save_confusion_matrix(test_metrics[selected_name]["confusion_matrix"],
                          f"Confusion Matrix — {selected_name} (chronological test)",
                          outputs_dir / "confusion_matrix.png")
    save_comparison(test_metrics, outputs_dir / "model_comparison.png")

    # Save full preprocessing+classifier pipeline.
    joblib.dump(best_model, models_dir / "best_model.pkl")
    joblib.dump(FEATURE_COLUMNS, models_dir / "feature_cols.pkl")
    location_encoder = LabelEncoder()
    location_encoder.fit(df["location"].astype(str))
    joblib.dump(location_encoder, models_dir / "location_encoder.pkl")

    metadata = {
        "project": "Traffic Bottleneck Prediction and Smart Routing System",
        "schema_version": "2.0",
        "model_type": "30-minute-ahead congestion classification",
        "selected_model": selected_name,
        "model_selection_rule": "Highest validation macro-F1 among Random Forest and XGBoost; persistence baseline is comparator only",
        "target": "Congestion label observed at the next consecutive 30-minute interval for the same location",
        "target_column": TARGET_COLUMN,
        "classes": {str(i): ID_TO_LABEL[i] for i in LABEL_IDS},
        "features": FEATURE_COLUMNS,
        "categorical_features": ["location"],
        "numeric_features": [c for c in FEATURE_COLUMNS if c != "location"],
        "excluded_contemporaneous_fields": ["vehicle_count", "avg_speed", "rainfall", "clouds", "temperature", "congestion_label"],
        "persistence_baseline": "predict target label = current_congestion_label at prediction origin; not supplied to the learned classifier",
        "split_strategy": "Chronological split by prediction_timestamp; all locations at a timestamp stay in one partition",
        "split_fractions": {"train": 0.70, "validation": 0.15, "test": 0.15},
        "train_rows": int(len(split.train)),
        "validation_rows": int(len(split.validation)),
        "test_rows": int(len(split.test)),
        "train_end": split.train_end,
        "validation_end": split.validation_end,
        "test_start": str(split.test.prediction_timestamp.min()),
        "test_end": str(split.test.prediction_timestamp.max()),
        "random_state": int(args.random_state),
        "dataset_source": "dataset/processed_bhubaneswar_traffic.csv",
        "dataset_domain": "Synthetic Bhubaneswar; not real-world observed traffic",
        "runtime_versions": {
            "python": __import__("sys").version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "xgboost": xgboost.__version__,
            "joblib": joblib.__version__,
        },
    }
    results = {
        "metadata": metadata,
        "validation_metrics_for_model_selection": validation_metrics,
        "holdout_test_metrics": test_metrics,
    }
    (models_dir / "model_metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    (outputs_dir / "phase1_metrics.json").write_text(json.dumps(results, indent=2, default=str))
    (outputs_dir / "model_comparison_summary.csv").write_text(pd.DataFrame([
        {"model": name, **{k: metrics[k] for k in ["accuracy", "precision_macro", "recall_macro", "f1_macro", "f1_weighted"]},
         "high_precision": metrics["high_class_error_analysis"]["precision"],
         "high_recall": metrics["high_class_error_analysis"]["recall"],
         "high_false_positives": metrics["high_class_error_analysis"]["false_positives"],
         "high_false_negatives": metrics["high_class_error_analysis"]["false_negatives"]}
        for name, metrics in test_metrics.items()
    ]).to_csv(index=False))

    print("[6/7] Saved model and reports:")
    print(f"  {models_dir / 'best_model.pkl'}")
    print(f"  {outputs_dir / 'phase1_metrics.json'}")
    print("[7/7] Selected model final test classification report:")
    print(pd.DataFrame(test_metrics[selected_name]["classification_report"]).T.to_string(float_format=lambda x: f"{x:.4f}"))
    print("\nPhase 1 baseline comparison and ML evaluation complete.")


if __name__ == "__main__":
    main()
