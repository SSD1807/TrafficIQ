from __future__ import annotations

import pandas as pd
import streamlit as st

from backend.dependencies import ROOT
from ui import status as S
from ui.context import Ctx, eval_json
from ui.theme import note, page_header


def render(ctx: Ctx) -> None:
    page_header("Model Evaluation", "Phase 1 classifier hold-out results and saved diagnostic plots", badges=[S.SYNTH])
    p1 = eval_json(str(ROOT / "outputs" / "phase1_metrics.json"))
    if not p1:
        st.info("Run `python phase2_train.py` to generate evaluation results."); return
    rows = []
    for k, m in p1["holdout_test_metrics"].items():
        h = m["high_class_error_analysis"]
        rows.append({"Model": k, "Accuracy": m["accuracy"], "Macro F1": m["f1_macro"], "Weighted F1": m["f1_weighted"],
                     "High precision": h["precision"], "High recall": h["recall"], "High FP": h["false_positives"], "High FN": h["false_negatives"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.3f") for c in ("Accuracy", "Macro F1", "Weighted F1", "High precision", "High recall")})
    note("All metrics are measured on a synthetic Bhubaneswar dataset and do not establish real-world performance.")
    imgs = [("Model comparison", "model_comparison.png"), ("Confusion matrix — Random Forest", "confusion_matrix_random_forest.png"),
            ("Confusion matrix — XGBoost", "confusion_matrix_xgboost.png"), ("Congestion by hour", "traffic_by_hour.png"),
            ("Congestion distribution", "congestion_distribution.png"), ("Traffic heatmap", "traffic_heatmap.png")]
    cols = st.columns(2)
    for i, (cap, fn) in enumerate(imgs):
        p = ROOT / "outputs" / fn
        if p.exists():
            cols[i % 2].image(str(p), caption=cap, width="stretch")
    with st.expander("Legacy simulation dashboard"):
        st.write("The original dashboard is preserved as `dashboard_legacy.py`.")
        st.code("streamlit run dashboard_legacy.py", language="bash")
