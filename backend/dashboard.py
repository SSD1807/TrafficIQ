"""Traffic Intelligence dashboard (v3) -- Bhubaneswar.

Run:  streamlit run dashboard.py
The previous dashboard is preserved as dashboard_v1.py and the original as dashboard_legacy.py.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Traffic Intelligence | Bhubaneswar", page_icon="🚦", layout="wide",
                   initial_sidebar_state="expanded")

from ui import status as S  # noqa: E402
from ui.context import build_ctx  # noqa: E402
from ui.pages import copilot, evaluation, forecasting, incidents, map_page, overview, prediction, route_planner, weather  # noqa: E402
from ui.theme import inject_css, badge  # noqa: E402

PAGES = {
    "Executive Overview": overview,
    "Traffic Map": map_page,
    "Traffic Prediction": prediction,
    "Forecasting Analytics": forecasting,
    "Route Planner": route_planner,
    "Incident Lab": incidents,
    "Weather": weather,
    "AI Copilot": copilot,
    "Model Evaluation": evaluation,
}

inject_css()
try:
    with st.spinner("Loading models and road network…"):
        ctx = build_ctx()
except Exception as exc:  # a broken artifact must not leave a blank page
    st.error(f"The dashboard could not start: {exc}")
    st.stop()

with st.sidebar:
    st.markdown('<div class="ti-brand">🚦 Traffic Intelligence</div><div class="ti-brand-sub">Bhubaneswar · bottleneck prediction & smart routing</div>',
                unsafe_allow_html=True)
    page = st.radio("Navigate", list(PAGES), label_visibility="collapsed", key="nav_page")
    st.divider()
    st.markdown("**Data sources**")
    st.markdown(f"Traffic &nbsp;{badge(S.SYNTH)}", unsafe_allow_html=True)
    st.markdown(f"Roads &nbsp;{badge(S.road_badge(ctx.provider.source))}", unsafe_allow_html=True)
    w = st.session_state.get("weather_result")
    st.markdown(f"Weather &nbsp;{badge(S.payload_badge(w))}", unsafe_allow_html=True)
    st.caption("Change light/dark under ⋮ → Settings → Theme.")

try:
    PAGES[page].render(ctx)
except Exception as exc:  # isolate page failures; keep navigation usable
    st.error(f"This section hit an error: {type(exc).__name__}: {exc}")
    with st.expander("Technical details"):
        st.exception(exc)
