# Phase 3 — Traffic forecasting report

## Scope and prediction targets

This is a separate pipeline from the Phase 1 next-interval congestion classifier. Forecasting targets are:

- `vehicle_count` at +30 minutes and +60 minutes.
- `avg_speed` at +30 minutes and +60 minutes.
- Congestion class at each horizon from a separately trained Random Forest classifier.

Features are location, target interval calendar context known in advance, lagged vehicle count/speed/weather at the forecast origin, and rolling means over the preceding 3 and 6 samples. No future target measurements are included in the feature matrix. A future-perturbation invariance test also changed all measurements strictly after one selected origin and confirmed that the origin feature row stayed identical while the future target changed.

## Evaluation protocol

For each horizon, examples are split chronologically by `target_timestamp` into 80% train and 20% test. Every regressor for a given horizon/target uses the same train/test rows. Baselines use only the last observation (persistence) and a 3-sample rolling average. Random Forest and XGBoost use fitted median imputation and one-hot encoding inside the pipeline.

Regression metrics: MAE, RMSE and SMAPE. The congestion classifier reports accuracy, macro precision/recall/F1 and per-class precision/recall/F1 with a confusion matrix.

## Executed numeric results

### 30-minute horizon

| Target | Model | MAE | RMSE | SMAPE |
|---|---|---:|---:|---:|
| Vehicle count | Persistence | 140.72 | 220.65 | 24.78% |
| Vehicle count | Moving Average | 162.26 | 241.69 | 30.81% |
| Vehicle count | Random Forest | **79.26** | **120.96** | **12.90%** |
| Vehicle count | XGBoost | 93.78 | 139.61 | 17.42% |
| Average speed | Persistence | 4.83 | 6.56 | 12.32% |
| Average speed | Moving Average | 4.49 | 6.10 | 11.43% |
| Average speed | Random Forest | **3.36** | **4.36** | **8.51%** |
| Average speed | XGBoost | 3.72 | 4.87 | 9.47% |

### 60-minute horizon

| Target | Model | MAE | RMSE | SMAPE |
|---|---|---:|---:|---:|
| Vehicle count | Persistence | 179.21 | 266.71 | 33.15% |
| Vehicle count | Moving Average | 211.17 | 304.85 | 40.99% |
| Vehicle count | Random Forest | **76.19** | **115.61** | **12.54%** |
| Vehicle count | XGBoost | 92.42 | 137.06 | 17.33% |
| Average speed | Persistence | 5.26 | 7.15 | 13.44% |
| Average speed | Moving Average | 5.08 | 6.92 | 12.96% |
| Average speed | Random Forest | **3.32** | **4.32** | **8.43%** |
| Average speed | XGBoost | 3.72 | 4.83 | 9.46% |

The panel test split contains 7,000 rows for each target/horizon. The results above are from the synthetic dataset only. The lower 60-minute RF error than 30-minute error reflects the dataset's synthetic temporal structure and should not be interpreted as a general property of traffic forecasting.

## Congestion-class forecast results

| Horizon | Accuracy | Macro F1 | High precision | High recall | High support |
|---|---:|---:|---:|---:|---:|
| 30 min | 95.40% | 88.09% | 56.71% | 91.93% | 285 |
| 60 min | 95.46% | 88.17% | 57.27% | 91.23% | 285 |

The class forecaster is a separate model. High recall is high but precision is lower due to false positives; it is not a substitute for reliable live incident or sensor data.

## SARIMA and LSTM decisions

A SARIMAX seasonal model (period=48 half-hour intervals) was run as a bounded rolling-origin pilot on Airport Road, limited to 24 test origins. At each origin it predicts one and two half-hour steps ahead, then appends the just-observed value to the state without refitting parameters. Results:

| Horizon | Target | MAE | RMSE | SMAPE | Test origins |
|---|---|---:|---:|---:|---:|
| 30 min | Vehicle count | 270.32 | 302.81 | 18.31% | 24 |
| 30 min | Average speed | 9.23 | 10.56 | 17.50% | 24 |
| 60 min | Vehicle count | 287.60 | 345.17 | 19.32% | 23 |
| 60 min | Average speed | 8.75 | 10.14 | 16.38% | 23 |

This is a small, one-location experiment and is not directly comparable to the 7,000-row panel regressors. A full 10-location SARIMA run was not performed in the final verification. The runner supports bounded experiments through `--max-locations` and `--max-test-points`; increase these values only after allowing for substantially longer runtimes. The pilot output is saved in `outputs/forecasting/sarima_metrics.json`.

LSTM was not trained. The included data is a short synthetic 10-location panel (about 73 days), TensorFlow was not installed in the validation environment, and no real-world series is available to justify the extra sequence-model capacity. A deep model on this source could create an impressive-looking but weakly validated result. The project should add LSTM only after richer observed data and a proper rolling-origin design are available.

## Outputs

- `models/forecasting/`: fitted 30/60-minute RF regressors and class classifiers.
- `outputs/forecasting/forecast_metrics.json`: panel model and baseline scores.
- `outputs/forecasting/confusion_matrix_classification_30m.png` and `...60m.png`.
- `outputs/forecasting/sarima_metrics.json`: one-location SARIMA pilot results.

Run `python -m forecasting.forecast_pipeline` to regenerate the primary forecast artifacts. Run `python -m forecasting.sarima_runner` for the optional SARIMA experiment.

**Interpretation limit:** no result in this report establishes real-world traffic forecasting accuracy.
