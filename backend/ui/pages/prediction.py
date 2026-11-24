from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from backend.dependencies import ROOT
from ui import status as S
from ui.context import Ctx, eval_json, predictions_table
from ui.theme import CONGESTION_COLORS, card, note, page_header, show_chart


@st.cache_data(show_spinner=False)
def feature_importance(_model, marker: str) -> pd.DataFrame:
    """Global importance of the deployed classifier, one-hot location columns collapsed to 'location'."""
    clf = _model.named_steps["classifier"]
    if not hasattr(clf, "feature_importances_"):
        return pd.DataFrame()
    names = _model.named_steps["preprocess"].get_feature_names_out()
    df = pd.DataFrame({"feature": names, "importance": clf.feature_importances_})
    df["feature"] = df.feature.str.replace(r"^(categorical|numeric)__", "", regex=True)
    df.loc[df.feature.str.startswith("location_"), "feature"] = "location"
    return df.groupby("feature", as_index=False).importance.sum().sort_values("importance", ascending=False)


def render(ctx: Ctx) -> None:
    state = ctx.traffic.get_current_state()
    page_header("Traffic Prediction", "Next-30-minute congestion class (Low / Medium / High) per monitored location",
                badges=[S.SYNTH])
    if ctx.traffic.model is None:
        st.error("Trained classifier not found. Run `python phase1_preprocess.py` then `python phase2_train.py`.")
        return
    ts = pd.Timestamp(state["data_timestamp"])
    note(f"Predictions are made from the last SYNTHETIC observation ({ts:%d %b %Y %H:%M}, {S.data_age_text(ts)}) for the interval "
         f"starting {ts + pd.Timedelta(minutes=30):%H:%M} of that replayed day. They are not forecasts of today's traffic.")

    preds = predictions_table(ctx.traffic, state["data_timestamp"])
    if "error" in preds and preds["error"].notna().any():
        st.warning("Prediction failed for: " + ", ".join(preds[preds.error.notna()].location))
    preds = preds[preds.predicted.notna()] if "predicted" in preds else preds

    # ---------------------------------------------------------------- all locations
    st.subheader("All locations")
    left, right = st.container(), st.container()  # stacked: the table needs the full width
    with left:
        view = preds.rename(columns={"location": "Location", "predicted": "Predicted class", "confidence": "Confidence"}).copy()
        pct_cols = ["Confidence", "P(Low)", "P(Medium)", "P(High)"]
        view[pct_cols] = view[pct_cols] * 100  # ProgressColumn formats raw numbers, so scale 0-1 to 0-100
        st.dataframe(view[["Location", "Predicted class"] + pct_cols], hide_index=True, width="stretch",
                     column_config={c: st.column_config.ProgressColumn(c, format="%.0f%%", min_value=0, max_value=100) for c in pct_cols})
    with right:
        long = preds.melt(id_vars="location", value_vars=["P(Low)", "P(Medium)", "P(High)"], var_name="Class", value_name="Probability")
        long["Class"] = long["Class"].str.extract(r"P\((.*)\)")
        fig = px.bar(long, x="Probability", y="location", color="Class", orientation="h", color_discrete_map=CONGESTION_COLORS,
                     category_orders={"Class": ["Low", "Medium", "High"]}, title="Class probabilities by location")
        fig.update_layout(barmode="stack", yaxis_title=None, xaxis_tickformat=".0%")
        show_chart(fig, 360)

    # ---------------------------------------------------------------- single location
    st.subheader("Location detail")
    c1, c2 = st.columns([2, 2])
    loc = c1.selectbox("Location", ctx.traffic.list_locations(), key="pred_loc")
    meta = eval_json(str(ROOT / "models" / "model_metadata.json")) or {}
    p1 = eval_json(str(ROOT / "outputs" / "phase1_metrics.json")) or {}
    deployed = meta.get("selected_model", "Random Forest")
    c2.selectbox("Model", [f"{deployed} (deployed)"], disabled=True,
                 help="Only the model selected by validation macro-F1 is saved as an artifact, so it is the only one that can run here. "
                      "Hold-out metrics for the other candidates are shown below.")

    with st.expander("What-if: override the last observation fed to the model"):
        h = ctx.traffic.history(loc, 1).iloc[-1]
        a, b, c = st.columns(3)
        vc = a.slider("Vehicle count", 0, int(max(2500, h.vehicle_count * 1.5)), int(h.vehicle_count))
        sp = b.slider("Average speed (km/h)", 5.0, 90.0, float(h.avg_speed), 0.5)
        rn = c.slider("Rainfall (mm)", 0.0, 20.0, float(h.rainfall), 0.1)
        use_over = st.checkbox("Apply overrides", False)
    overrides = {"vehicle_count": vc, "avg_speed": sp, "rainfall": rn} if use_over else None
    try:
        p = ctx.traffic.predict_next(loc, overrides=overrides)
    except Exception as exc:
        st.error(f"Prediction failed: {exc}"); return

    probs = p["class_probabilities"] or {}
    conf = max(probs.values()) if probs else None
    m1, m2, m3 = st.columns(3)
    m1.markdown(card("Predicted class", p["predicted_congestion"], f"Next 30 min · {p['model_name']}", S.SYNTH), unsafe_allow_html=True)
    m2.markdown(card("Confidence", f"{conf:.0%}" if conf is not None else "n/a", "Highest class probability (not a calibrated accuracy)",
                     help_text="Random Forest vote share. It is not guaranteed to be calibrated."), unsafe_allow_html=True)
    inp = p["input_measurements"]
    m3.markdown(card("Model inputs", f"{inp['lag_vehicle_count']:.0f} veh · {inp['lag_avg_speed']:.1f} km/h",
                     "Last observation" + (" (what-if override)" if use_over else "")), unsafe_allow_html=True)
    st.write("")

    g1, g2 = st.columns(2)
    with g1:
        if probs:
            fig = go.Figure(go.Bar(x=list(probs), y=list(probs.values()), marker_color=[CONGESTION_COLORS[k] for k in probs],
                                   text=[f"{v:.0%}" for v in probs.values()], textposition="outside"))
            fig.update_layout(title="Class probabilities", yaxis_tickformat=".0%", yaxis_range=[0, 1.1])
            show_chart(fig, 320)
    with g2:
        hist = ctx.traffic.history(loc, 48)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=hist.timestamp, y=hist.vehicle_count, name="Vehicles", line=dict(color="#2563eb")), secondary_y=False)
        fig.add_trace(go.Scatter(x=hist.timestamp, y=hist.avg_speed, name="Avg speed (km/h)", line=dict(color="#d97706")), secondary_y=True)
        fig.update_layout(title="Last 24 h of synthetic observations", xaxis_title="Dataset timestamp (synthetic)")
        show_chart(fig, 320)

    with st.expander("Historical observations (synthetic)"):
        h = ctx.traffic.history(loc, 48).sort_values("timestamp", ascending=False)
        st.dataframe(h[["timestamp", "vehicle_count", "avg_speed", "rainfall", "clouds", "temperature", "congestion_label"]],
                     hide_index=True, width="stretch")

    # ---------------------------------------------------------------- explanations
    st.subheader("Model insight")
    e1, e2 = st.columns([3, 2])
    with e1:
        imp = feature_importance(ctx.traffic.model, str(meta.get("selected_model")))
        if not imp.empty:
            fig = px.bar(imp.head(10).iloc[::-1], x="importance", y="feature", orientation="h", title="Global feature importance (deployed model)")
            fig.update_layout(yaxis_title=None, xaxis_title="Share of impurity reduction")
            show_chart(fig, 340)
            st.caption("Global importance describes the model overall, not why this single prediction was made. "
                       "Per-prediction explanations (e.g. SHAP) are not included.")
    with e2:
        hold = (p1.get("holdout_test_metrics") or {})
        if hold:
            rows = [{"Model": k, "Accuracy": v["accuracy"], "Macro F1": v["f1_macro"], "High recall": v["high_class_error_analysis"]["recall"]}
                    for k, v in hold.items()]
            st.markdown("**Hold-out comparison** (synthetic data)")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%.3f") for c in ("Accuracy", "Macro F1", "High recall")})
            st.caption(f"Deployed: {deployed}, selected by validation macro-F1. The persistence baseline is a comparator only. Metrics do not prove real-world accuracy.")
