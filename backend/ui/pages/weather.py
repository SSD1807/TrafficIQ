from __future__ import annotations

import streamlit as st

from ui import status as S
from ui.context import Ctx, weather_cached
from ui.theme import card, note, page_header


def render(ctx: Ctx) -> None:
    w = st.session_state.get("weather_result")
    page_header("Weather", "Current conditions for Bhubaneswar from Open-Meteo", badges=[S.payload_badge(w)])
    c1, c2 = st.columns([1, 3])
    allow = c2.checkbox("Allow clearly-labelled simulated fallback if Open-Meteo is unreachable", False)
    if c1.button("Fetch current weather", type="primary"):
        with st.spinner("Requesting Open-Meteo…"):
            st.session_state["weather_result"] = ctx.weather.get_current(allow_simulated_fallback=allow, force_refresh=True)
        st.rerun()
    if not w:
        st.info("No weather result yet."); return
    if w.get("status") in {"ok", "stale_cache", "simulated_fallback"}:
        cols = st.columns(4)
        for col, (lbl, val, sub) in zip(cols, [("Temperature", f"{w.get('temperature_c')} °C", ""), ("Rain", f"{w.get('rain_mm')} mm", "Current hour"),
                                              ("Wind", f"{w.get('wind_speed_kmh')} km/h", ""), ("Cloud cover", f"{w.get('cloud_cover_pct')} %", "")]):
            col.markdown(card(lbl, val, sub, S.payload_badge(w)), unsafe_allow_html=True)
        st.caption(f"Source: {w.get('source')} · observed/modelled at {w.get('observed_at', w.get('time', 'n/a'))}")
        note(w.get("notice") or w.get("attribution") or "Open-Meteo values are model-derived current conditions.")
    else:
        st.error(w.get("message") or w.get("error") or "Weather is currently unavailable.")
        st.caption("The dashboard never invents weather values. Check your internet connection or firewall for api.open-meteo.com.")
