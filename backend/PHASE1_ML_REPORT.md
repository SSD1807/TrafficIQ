# Phase 1 — Independent data/ML validation report

## Prediction contract

- **Prediction unit:** one location and one 30-minute observation interval.
- **Target:** congestion class at the next consecutive 30-minute interval for the same location.
- **Classes:** Low=0, Medium=1, High=2.
- **Prediction origin:** the current interval. Current vehicle count, speed and weather are available as lagged measurements when the next interval is predicted.
- **Model features:** location; future interval's calendar features known in advance; current/previous observations of vehicle count, speed, rainfall, clouds and temperature.
- **Excluded model inputs:** contemporaneous target-period vehicle count, speed, weather, congestion label, and target label name.
- **Persistence comparator:** uses current congestion label to predict the next label. This is evaluated separately and is not supplied to the learned classifier.

## Dataset strategy

`dataset/bhubaneswar_synthetic_traffic.csv` is the sole primary source for the Bhubaneswar classifier. It contains 35,000 synthetic observations across 10 locations at a 30-minute cadence from 2023-01-01 00:00 through 2023-03-14 21:30. The processed supervised set contains 34,990 consecutive-interval examples.

The Metro Interstate Traffic Volume file remains a separate dataset. It is an hourly US interstate series with different measurement semantics and no equivalent Bhubaneswar location/average-speed target. It is not concatenated into the Bhubaneswar model training table.

## Leakage controls and preprocessing

- Parse and sort observations by location and timestamp.
- Deduplicate `(location, timestamp)` keys.
- Pair only observations exactly 30 minutes apart; gaps do not create longer-horizon examples.
- Shift observed sensor/weather fields into explicit `lag_*` columns.
- Compute calendar features from the target timestamp only, because time of day/day of week are known before the prediction interval.
- Keep `current_congestion_label` only for the persistence baseline.
- Fit numeric median imputation and categorical most-frequent imputation/one-hot encoding inside each model Pipeline, so holdout distributions do not fit preprocessing.
- OneHotEncoder uses `handle_unknown='ignore'` so new locations do not crash inference.
- Split by unique target timestamps, with all locations for one target timestamp in the same partition.
- Model selection uses validation macro-F1; final test is not used for model selection.

## Chronological split

| Partition | Rows | Target timestamp range |
|---|---:|---|
| Train | 24,490 | 2023-01-01 00:30 – 2023-02-21 00:30 |
| Validation | 5,240 | 2023-02-21 01:00 – 2023-03-03 22:30 |
| Test | 5,260 | 2023-03-03 23:00 – 2023-03-14 21:30 |

All three models below are scored on exactly the same 5,260-row chronological test partition.

## Reproduced test metrics

| Model | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 |
|---|---:|---:|---:|---:|---:|
| Persistence baseline | 91.24% | 78.70% | 78.72% | 78.71% | 91.24% |
| Random Forest | 96.06% | 86.55% | 89.79% | 88.03% | 96.14% |
| XGBoost | 95.10% | 82.62% | 94.78% | 86.78% | 95.51% |

Random Forest was selected based on validation macro-F1 (0.8967 vs 0.8932 for XGBoost). The persistence baseline is a comparator, not a candidate for fitted-model selection.

### High-congestion error analysis

| Model | High support | True positives | False positives | False negatives | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|
| Persistence | 190 | 97 | 93 | 93 | 51.05% | 51.05% |
| Random Forest | 190 | 143 | 78 | 47 | 64.71% | 75.26% |
| XGBoost | 190 | 176 | 162 | 14 | 52.07% | 92.63% |

The High class is sparse (190 test rows). XGBoost has higher High recall but produces more false positives. Random Forest is the selected overall macro-F1 model and has a better High precision/false-positive tradeoff, but it still misses 46 High cases. These are important limitations for any safety-related use.

## Artifacts

- `models/best_model.pkl`: fitted preprocessing + Random Forest classifier Pipeline.
- `models/feature_cols.pkl`: canonical feature schema.
- `models/model_metadata.json`: model schema and provenance.
- `outputs/phase1_metrics.json`: validation and holdout reports for all three models.
- `outputs/confusion_matrix_*.png`: confusion matrices for persistence, RF and XGBoost.
- `outputs/model_comparison.png`: comparable holdout chart.

## Reproducibility and scope

Training is seeded with `random_state=42`. Re-run `python phase1_preprocess.py && python phase2_train.py` to reproduce the split and metrics. The final release was trained using the environment versions recorded in `models/model_metadata.json`.

**Interpretation limit:** all labels and observations originate from a synthetic generator. Metrics quantify how well the model generalizes to a later time window from that generator. They are not a measurement of real-world Bhubaneswar traffic accuracy. A real-world claim requires real detector/GPS/travel-time data, consistent label definitions and prospective evaluation.
