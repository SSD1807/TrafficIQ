from __future__ import annotations

import os
from importlib.util import find_spec

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui import status as S
from ui.context import Ctx, predictions_table, weather_cached
from ui.theme import CONGESTION_COLORS, card, cards_row, note, page_header, status_row, show_chart


def render(ctx: Ctx) -> None:
    state = ctx.traffic.get_current_state()
    page_header("Executive Overview", "System status, congestion picture and data provenance for Bhubaneswar",
                badges=[S.SYNTH if state.get("status") == "ok" else S.UNAVAIL])

    if state.get("status") != "ok":
        st.error("The synthetic traffic dataset could not be loaded. Restore `dataset/bhubaneswar_synthetic_traffic.csv` "
                 "and the Phase 1 model artifacts, then reload.")
        return

    ts = pd.Timestamp(state["data_timestamp"])
    note(f"Traffic figures come from the bundled SYNTHETIC dataset. Latest record: {ts:%d %b %Y, %H:%M} "
         f"({S.data_age_text(ts)}). This is not live road traffic.")

    # Weather is fetched once per session; failures are cached so the page never hangs on retries.
    if "weather_checked" not in st.session_state:
        with st.spinner("Checking Open-Meteo…"):
            st.session_state["weather_result"] = weather_cached(ctx.weather)
        st.session_state["weather_checked"] = True
    weather = st.session_state.get("weather_result")
    w_badge = S.payload_badge(weather)

    preds = predictions_table(ctx.traffic, state["data_timestamp"])
    ok_preds = preds[preds.get("predicted").notna()] if "predicted" in preds else preds.iloc[0:0]
    n_high = int((ok_preds.predicted == "High").sum())
    rows = state["locations"]
    avg_speed = sum(r["avg_speed"] for r in rows) / len(rows)
    active = ctx.incidents.list_active()

    # ---- health
    health = _health(ctx, weather)
    overall = "DOWN" if any(h[2] == "DOWN" for h in health) else "DEGRADED" if any(h[2] == "DEGRADED" for h in health) else "OK"

    cards_row([
        dict(label="System health", value={"OK": "Operational", "DEGRADED": "Degraded", "DOWN": "Down"}[overall],
             sub=f"{sum(h[2] == 'OK' for h in health)}/{len(health)} components healthy", chip=overall),
        dict(label="Monitored locations", value=str(len(rows)), sub="Synthetic monitoring points", chip=S.SYNTH),
        dict(label="Predicted High (next 30 min)", value=f"{n_high} of {len(ok_preds)}",
             sub="Random Forest on synthetic history", chip=S.SYNTH,
             help_text="Count of locations whose predicted next-interval class is High."),
        dict(label="Avg estimated speed", value=f"{avg_speed:.1f} km/h",
             sub=f"Mean across locations at {ts:%d %b %Y %H:%M}", chip=S.SYNTH),
    ])
    st.write("")
    wtxt = f"{weather.get('temperature_c')} °C · {weather.get('rain_mm')} mm rain" if weather and weather.get("status") in {"ok", "stale_cache", "simulated_fallback"} else "No weather data"
    road = ctx.provider.summary()
    cards_row([
        dict(label="Active incidents", value=str(len(active)),
             sub="Manual / simulated (in-memory)" if active else "None registered", chip=S.SYNTH if active else None),
        dict(label="Weather (Bhubaneswar)", value=wtxt, sub=(weather or {}).get("source") if w_badge != S.UNAVAIL else "Open-Meteo unreachable · no values shown", chip=w_badge),
        dict(label="Road network", value=f"{road['nodes']:,} nodes", sub=f"{road['edges']:,} edges · {ctx.provider.source.replace('_', ' ')}",
             chip=S.road_badge(ctx.provider.source)),
        dict(label="Congestion model", value=(ok_preds.model.iloc[0] if len(ok_preds) else "n/a"),
             sub="Chronological hold-out evaluated", chip=S.SYNTH),
    ])
    st.write("")

    left, right = st.columns([3, 2])
    with left:
        cur = S.congestion_counts(rows); pred = S.congestion_counts(ok_preds.to_dict("records"), "predicted")
        fig = go.Figure()
        for name, d in (("Latest synthetic snapshot", cur), ("Predicted next 30 min", pred)):
            fig.add_bar(name=name, x=list(d), y=list(d.values()),
                        text=list(d.values()), textposition="outside")
        fig.update_layout(barmode="group", title="Congestion distribution across locations (synthetic)",
                          yaxis_title="Locations", xaxis_title="Congestion class")
        show_chart(fig, 320)
    with right:
        st.markdown("**Data-source status**")
        tomtom_on = bool(os.getenv("TOMTOM_API_KEY"))
        html = "".join([
            status_row("Traffic observations", f"Bundled synthetic dataset · {ts:%Y-%m-%d}", S.SYNTH),
            status_row("Road geometry", f"{road['source'].replace('_', ' ')} · static map data, not traffic", S.road_badge(ctx.provider.source)),
            status_row("Weather", (weather or {}).get("source", "Open-Meteo") if w_badge != S.UNAVAIL else "Open-Meteo unreachable · no values shown", w_badge),
            status_row("TomTom live traffic", "API key configured" if tomtom_on else "TOMTOM_API_KEY not set", S.UNAVAIL if not tomtom_on else "CONFIGURED"),
            status_row("Incidents", f"{len(active)} active · manual/simulated", S.SYNTH),
        ])
        st.markdown(html, unsafe_allow_html=True)
        if st.button("Refresh weather", key="ov_weather"):
            st.session_state["weather_result"] = weather_cached(ctx.weather, force=int(pd.Timestamp.now().timestamp()))
            st.rerun()

    st.markdown("**Component health**")
    st.markdown("".join(status_row(n, d, l) for n, d, l in health), unsafe_allow_html=True)

    st.markdown("**Location snapshot**")
    snap = pd.DataFrame(rows).merge(ok_preds[["location", "predicted", "confidence"]], on="location", how="left")
    snap["confidence"] = snap["confidence"] * 100  # shown as a 0-100 percentage
    snap = snap.rename(columns={"vehicle_count": "Vehicles", "avg_speed": "Avg speed (km/h)", "congestion_label": "Current class (synthetic)",
                                "predicted": "Predicted next 30 min", "confidence": "Confidence", "location": "Location"})
    st.dataframe(snap[["Location", "Vehicles", "Avg speed (km/h)", "Current class (synthetic)", "Predicted next 30 min", "Confidence"]],
                 hide_index=True, width="stretch",
                 column_config={"Confidence": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100)})


def _health(ctx: Ctx, weather) -> list[tuple[str, str, str]]:
    from backend.dependencies import ROOT
    h: list[tuple[str, str, str]] = []
    h.append(("Congestion classifier", "models/best_model.pkl" if ctx.traffic.model is not None else "Model artifact missing", "OK" if ctx.traffic.model is not None else "DOWN"))
    fc = (ROOT / "models" / "forecasting").exists() and bool(ctx.forecasts.available_regressors(30))
    h.append(("Forecasting models (30/60 min)", "Random Forest + XGBoost artifacts found" if fc else "Artifacts missing", "OK" if fc else "DOWN"))
    h.append(("Road network", ctx.provider.source.replace("_", " ") + (f" · {ctx.provider.last_error}" if ctx.provider.last_error and not ctx.provider.is_real_osm else ""),
              "OK" if ctx.provider.is_real_osm else "DEGRADED"))
    h.append(("Weather adapter", (weather or {}).get("source", "not fetched") if S.payload_badge(weather) != S.UNAVAIL else (weather or {}).get("message", "Open-Meteo unreachable"),
              "OK" if S.payload_badge(weather) in {S.LIVE, S.CACHED} else "DEGRADED"))
    ai = "OpenAI configured" if os.getenv("OPENAI_API_KEY") and find_spec("openai") else "Rule-based fallback active"
    h.append(("AI Traffic Copilot", ai, "OK"))
    return h
