from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from backend.dependencies import ROOT
from ui import status as S
from ui.context import Ctx, eval_json, now_ist
from ui.theme import CONGESTION_COLORS, card, note, page_header, show_chart

TARGET_LABEL = {"vehicle_count": "Vehicle count", "avg_speed": "Average speed (km/h)"}


def render(ctx: Ctx) -> None:
    page_header("Forecasting Analytics", "30- and 60-minute vehicle-count, speed and congestion forecasts with model benchmarks",
                badges=[S.SYNTH])
    metrics = eval_json(str(ROOT / "outputs" / "forecasting" / "forecast_metrics.json"))
    if not ctx.forecasts.available_regressors(30):
        st.error("Forecast model artifacts are missing. Run `python -m forecasting.forecast_pipeline`.")
        return

    c1, c2, c3 = st.columns([2, 1.4, 1.6])
    loc = c1.selectbox("Location", ctx.traffic.list_locations(), key="fc_loc")
    horizon = c2.radio("Horizon", [30, 60], horizontal=True, format_func=lambda h: f"{h} minutes", key="fc_h")
    avail = ctx.forecasts.available_regressors(horizon)
    model = c3.selectbox("Regressor", avail, format_func=lambda m: ctx.forecasts.REGRESSOR_MODELS[m], key="fc_model")

    try:
        f = ctx.forecasts.forecast(loc, horizon, model)
    except Exception as exc:
        st.error(f"Forecast failed: {exc}"); return
    origin, target = pd.Timestamp(f["origin_timestamp"]), pd.Timestamp(f["target_timestamp"])
    note(f"Forecast origin {origin:%d %b %Y %H:%M} → target {target:%d %b %Y %H:%M} (+{horizon} min). "
         f"Both timestamps belong to the SYNTHETIC dataset ({S.data_age_text(origin)}); the run time is {now_ist():%d %b %Y %H:%M} IST. "
         "This is a demonstration forecast, not a prediction for the current moment.")

    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(card("Forecast vehicles", f"{f['vehicle_count']:.0f}" if f.get("vehicle_count") is not None else "n/a", f"at {target:%H:%M} · {horizon} min ahead", S.SYNTH), unsafe_allow_html=True)
    k2.markdown(card("Forecast avg speed", f"{f['avg_speed']:.1f} km/h" if f.get("avg_speed") is not None else "n/a", f"at {target:%H:%M}", S.SYNTH), unsafe_allow_html=True)
    k3.markdown(card("Congestion class", f.get("predicted_congestion") or "n/a", f["forecast_model"]), unsafe_allow_html=True)
    probs = f.get("congestion_probabilities") or {}
    k4.markdown(card("Class confidence", f"{max(probs.values()):.0%}" if probs else "n/a", "Highest class probability"), unsafe_allow_html=True)
    st.write("")

    # ----------------------------------------------------------- history + forecast
    hist = ctx.traffic.history(loc, 24)
    g1, g2 = st.columns(2)
    for col, tgt in zip((g1, g2), ("vehicle_count", "avg_speed")):
        with col:
            fig = go.Figure()
            fig.add_scatter(x=hist.timestamp, y=hist[tgt], name="Actual (synthetic history)", line=dict(color="#2563eb"))
            if f.get(tgt) is not None:
                fig.add_scatter(x=[hist.timestamp.iloc[-1], target], y=[hist[tgt].iloc[-1], f[tgt]], mode="lines+markers",
                                name=f"Forecast (+{horizon} min)", line=dict(color="#dc2626", dash="dot"), marker=dict(size=[0, 11]))
            fig.update_layout(title=f"{TARGET_LABEL[tgt]} — actual and forecast", xaxis_title="Dataset timestamp (synthetic)",
                              yaxis_title=TARGET_LABEL[tgt])
            show_chart(fig, 330)

    if probs:
        fig = go.Figure(go.Bar(x=list(probs), y=list(probs.values()), marker_color=[CONGESTION_COLORS[k] for k in probs],
                               text=[f"{v:.0%}" for v in probs.values()], textposition="outside"))
        fig.update_layout(title=f"Congestion class probabilities at +{horizon} min", yaxis_tickformat=".0%", yaxis_range=[0, 1.1])
        show_chart(fig, 280)

    # ----------------------------------------------------------- model comparison
    st.subheader(f"Model comparison — {horizon}-minute horizon")
    if not metrics:
        st.info("Run `python -m forecasting.forecast_pipeline` to generate benchmark metrics."); return
    split = metrics["results"][f"{horizon}m"]
    sp = split.get("split", {})
    st.caption(f"Chronological hold-out: {sp.get('test_rows', '?'):,} test rows, targets {sp.get('test_target_start', '?')} → {sp.get('test_target_end', '?')} "
               "(synthetic). Lower is better for all three metrics.")
    rows = []
    for tgt, models in split["targets"].items():
        for name, m in models.items():
            rows.append({"Target": TARGET_LABEL[tgt], "Model": name, "MAE": m["mae"], "RMSE": m["rmse"], "SMAPE (%)": m["smape_pct"]})
    df = pd.DataFrame(rows)
    sel = st.radio("Target", list(df.Target.unique()), horizontal=True, key="fc_cmp_target")
    sub = df[df.Target == sel]
    tabs = st.tabs(["MAE", "RMSE", "SMAPE (%)", "Table"])
    for tab, metric in zip(tabs[:3], ("MAE", "RMSE", "SMAPE (%)")):
        with tab:
            fig = px.bar(sub.sort_values(metric), x="Model", y=metric, color="Model", text=sub.sort_values(metric)[metric].round(2))
            fig.update_layout(showlegend=False, title=f"{metric} — {sel}")
            show_chart(fig, 300)
    with tabs[3]:
        st.dataframe(sub, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ("MAE", "RMSE", "SMAPE (%)")})
    best = sub.sort_values("MAE").iloc[0]
    st.caption(f"Lowest MAE for {sel}: **{best.Model}**. Persistence and moving average are baselines. "
               "SARIMA is a separate rolling-origin experiment and LSTM was deliberately not trained (see FORECASTING_REPORT.md).")
    cls = split.get("congestion_classification")
    if cls:
        with st.expander("Class-forecast metrics"):
            st.json(cls, expanded=False)
    sar = eval_json(str(ROOT / "outputs" / "forecasting" / "sarima_metrics.json"))
    if sar:
        with st.expander("SARIMA pilot (single-location experiment)"):
            st.json(sar.get("results", sar), expanded=False)
