"""Rewrite the numeric tables in the Markdown reports from the metric JSON files.

Keeps the documentation exactly in sync with the saved artifacts after any retraining:
    python scripts/render_metric_tables.py          # rewrite in place
    python scripts/render_metric_tables.py --check  # exit 1 if any report is stale
Tables are located by their header row, so the surrounding prose is untouched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P1 = json.loads((ROOT / "outputs" / "phase1_metrics.json").read_text())["holdout_test_metrics"]
FC = json.loads((ROOT / "outputs" / "forecasting" / "forecast_metrics.json").read_text())["results"]
MODEL_ORDER = ["Persistence baseline", "Random Forest", "XGBoost"]
TARGETS = [("vehicle_count", "Vehicle count"), ("avg_speed", "Average speed")]
pct = lambda v: f"{v * 100:.2f}%"


def phase1_main() -> list[str]:
    rows = ["| Model | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 |", "|---|---:|---:|---:|---:|---:|"]
    for k in MODEL_ORDER:
        m = P1[k]; rows.append(f"| {k} | {pct(m['accuracy'])} | {pct(m['precision_macro'])} | {pct(m['recall_macro'])} | {pct(m['f1_macro'])} | {pct(m['f1_weighted'])} |")
    return rows


def phase1_high() -> list[str]:
    rows = ["| Model | High support | True positives | False positives | False negatives | Precision | Recall |", "|---|---:|---:|---:|---:|---:|---:|"]
    for k in MODEL_ORDER:
        h = P1[k]["high_class_error_analysis"]
        rows.append(f"| {k.replace(' baseline', '')} | {h['support']} | {h['true_positives']} | {h['false_positives']} | {h['false_negatives']} | {pct(h['precision'])} | {pct(h['recall'])} |")
    return rows


def summary_table() -> list[str]:
    rows = ["| Model | Accuracy | Macro-F1 | High precision | High recall | High FP | High FN |", "|---|---:|---:|---:|---:|---:|---:|"]
    for k in MODEL_ORDER:
        m = P1[k]; h = m["high_class_error_analysis"]
        rows.append(f"| {k.replace(' baseline', '')} | {pct(m['accuracy'])} | {pct(m['f1_macro'])} | {pct(h['precision'])} | {pct(h['recall'])} | {h['false_positives']} | {h['false_negatives']} |")
    return rows


def forecast_table(horizon: str) -> list[str]:
    rows = ["| Target | Model | MAE | RMSE | SMAPE |", "|---|---|---:|---:|---:|"]
    for key, label in TARGETS:
        models = FC[horizon]["targets"][key]
        best = min(models, key=lambda n: models[n]["mae"])
        for name, m in models.items():
            f = (lambda s: f"**{s}**") if name == best else (lambda s: s)
            rows.append(f"| {label} | {name} | {f(f'{m['mae']:.2f}')} | {f(f'{m['rmse']:.2f}')} | {f(f'{m['smape_pct']:.2f}%')} |")
    return rows


def class_forecast() -> list[str]:
    rows = ["| Horizon | Accuracy | Macro F1 | High precision | High recall | High support |", "|---|---:|---:|---:|---:|---:|"]
    for h in ("30m", "60m"):
        c = FC[h]["congestion_classification"]; hi = c["classification_report"]["High"]
        rows.append(f"| {h[:-1]} min | {pct(c['accuracy'])} | {pct(c['f1_macro'])} | {pct(hi['precision'])} | {pct(hi['recall'])} | {int(hi['support'])} |")
    return rows


def rf_integration() -> list[str]:
    rows = ["| Horizon | Target | RF MAE | RF RMSE | RF SMAPE |", "|---|---|---:|---:|---:|"]
    for h in ("30m", "60m"):
        for key, label in (("vehicle_count", "Vehicle count"), ("avg_speed", "Average speed (km/h)")):
            m = FC[h]["targets"][key]["Random Forest"]
            rows.append(f"| {h[:-1]} min | {label} | {m['mae']:.2f} | {m['rmse']:.2f} | {m['smape_pct']:.2f}% |")
    return rows


HEADERS = {  # header row -> list of generators, applied in document order
    "| Model | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 |": [phase1_main],
    "| Model | High support | True positives | False positives | False negatives | Precision | Recall |": [phase1_high],
    "| Model | Accuracy | Macro-F1 | High precision | High recall | High FP | High FN |": [summary_table],
    "| Target | Model | MAE | RMSE | SMAPE |": [lambda: forecast_table("30m"), lambda: forecast_table("60m")],
    "| Horizon | Accuracy | Macro F1 | High precision | High recall | High support |": [class_forecast],
    "| Horizon | Target | RF MAE | RF RMSE | RF SMAPE |": [rf_integration],
}


def rewrite(text: str) -> str:
    lines = text.split("\n"); out: list[str] = []; i = 0; seen: dict[str, int] = {}
    while i < len(lines):
        if lines[i] in HEADERS:
            n = seen.get(lines[i], 0); gens = HEADERS[lines[i]]
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            out.extend(gens[min(n, len(gens) - 1)]()); seen[lines[i]] = n + 1; i = j
        else:
            out.append(lines[i]); i += 1
    t = "\n".join(out)
    v = {k: P1[k].get("validation_selection_macro_f1") for k in ("Random Forest", "XGBoost")}
    if all(v.values()):
        t = re.sub(r"validation macro-F1 \(\d\.\d+ vs \d\.\d+ for XGBoost\)", f"validation macro-F1 ({v['Random Forest']:.4f} vs {v['XGBoost']:.4f} for XGBoost)", t)
    return t


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); args = ap.parse_args()
    stale = []
    for path in sorted(ROOT.glob("*.md")):
        old = path.read_text(); new = rewrite(old)
        if new != old:
            stale.append(path.name)
            if not args.check:
                path.write_text(new)
    print(("STALE: " if args.check else "updated: ") + (", ".join(stale) or "none"))
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
