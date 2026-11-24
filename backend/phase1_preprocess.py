"""Phase 1A: preprocess Bhubaneswar traffic and build 30-minute labels.

Run from any working directory:
    python phase1_preprocess.py
    python phase1_preprocess.py --input dataset/bhubaneswar_synthetic_traffic.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from ml_pipeline import build_supervised_dataset, normalize_bhubaneswar_data

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="dataset/bhubaneswar_synthetic_traffic.csv")
    parser.add_argument("--output", default="dataset/processed_bhubaneswar_traffic.csv")
    parser.add_argument("--outputs-dir", default="outputs")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    outputs_dir = Path(args.outputs_dir)
    if not outputs_dir.is_absolute():
        outputs_dir = ROOT / outputs_dir
    outputs_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"Bhubaneswar input dataset not found: {input_path}")

    print(f"[1/5] Reading Bhubaneswar-only source: {input_path}")
    raw = pd.read_csv(input_path)
    clean = normalize_bhubaneswar_data(raw)
    print(f"  Clean source rows: {len(clean):,}; locations: {clean['location'].nunique()}")

    print("[2/5] Building next-30-minute supervised examples...")
    supervised = build_supervised_dataset(clean)
    supervised.to_csv(output_path, index=False)
    print(f"  Supervised examples: {len(supervised):,} -> {output_path}")

    # Compatibility file for the existing Streamlit analytics panel. This is
    # deliberately Bhubaneswar-only; Metro Interstate rows are not concatenated.
    compat_path = ROOT / "dataset" / "merged_traffic.csv"
    clean.to_csv(compat_path, index=False)
    print(f"[3/5] Dashboard-compatible Bhubaneswar data -> {compat_path}")

    # Preserve the former cleaned-data filename for older local workflows.
    legacy_clean_path = ROOT / "clean_traffic.csv"
    clean.to_csv(legacy_clean_path, index=False)

    print("[4/5] Generating data-quality and EDA charts...")
    clean["hour"] = clean["timestamp"].dt.hour
    clean["day"] = clean["timestamp"].dt.dayofweek

    hourly = clean.groupby("hour")["vehicle_count"].mean()
    fig, ax = plt.subplots(figsize=(10, 4))
    hourly.plot(kind="bar", ax=ax)
    ax.set(title="Bhubaneswar Mean Vehicle Count by Hour", xlabel="Hour", ylabel="Vehicles / 30 min")
    fig.tight_layout()
    fig.savefig(outputs_dir / "traffic_by_hour.png", dpi=130)
    plt.close(fig)

    daily = clean.groupby("day")["vehicle_count"].mean().reindex(range(7))
    fig, ax = plt.subplots(figsize=(8, 4))
    daily.plot(kind="bar", ax=ax)
    ax.set(title="Bhubaneswar Mean Vehicle Count by Day", xlabel="Day of week (0=Monday)", ylabel="Vehicles / 30 min")
    fig.tight_layout()
    fig.savefig(outputs_dir / "traffic_by_day.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    order = ["Low", "Medium", "High"]
    label_counts = clean["congestion_label"].value_counts().reindex(order, fill_value=0)
    label_counts.plot(kind="bar", ax=ax)
    ax.set(title="Observed Congestion Label Distribution", xlabel="Current interval label", ylabel="Rows")
    ax.tick_params(axis="x", rotation=0)
    fig.tight_layout()
    fig.savefig(outputs_dir / "congestion_distribution.png", dpi=130)
    plt.close(fig)

    pivot = clean.pivot_table(values="vehicle_count", index="hour", columns="day", aggfunc="mean")
    pivot = pivot.reindex(index=range(24), columns=range(7))
    fig, ax = plt.subplots(figsize=(9, 6))
    sns.heatmap(pivot, cmap="YlOrRd", ax=ax)
    ax.set(title="Mean Vehicle Count: Hour vs Day", xlabel="Day of week", ylabel="Hour")
    fig.tight_layout()
    fig.savefig(outputs_dir / "traffic_heatmap.png", dpi=130)
    plt.close(fig)

    summary = {
        "source_file": str(input_path.relative_to(ROOT) if input_path.is_relative_to(ROOT) else input_path),
        "source_rows": int(len(clean)),
        "supervised_rows": int(len(supervised)),
        "location_count": int(clean["location"].nunique()),
        "timestamp_start": clean["timestamp"].min().isoformat(),
        "timestamp_end": clean["timestamp"].max().isoformat(),
        "observed_label_counts": {str(k): int(v) for k, v in clean["congestion_label"].value_counts().items()},
        "next_30m_target_counts": {str(k): int(v) for k, v in supervised["target_label_name"].value_counts().items()},
        "dataset_policy": "Bhubaneswar synthetic data is the primary model domain; Metro Interstate is retained separately.",
    }
    (outputs_dir / "phase1_data_summary.json").write_text(json.dumps(summary, indent=2))
    print("[5/5] Phase 1 preprocessing complete.")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
