from __future__ import annotations

import os

import streamlit as st

from backend.dependencies import get_copilot
from ui.context import Ctx
from ui.theme import note, page_header


def render(ctx: Ctx) -> None:
    page_header("AI Traffic Copilot", "Ask about congestion, forecasts, weather, incidents and routes — answers come from the same services as this dashboard")
    llm = bool(os.getenv("OPENAI_API_KEY"))
    st.caption("Mode: " + ("OpenAI tool-calling with rule-based fallback" if llm else "rule-based assistant (set OPENAI_API_KEY in .env to enable the LLM)"))
    hist = st.session_state.setdefault("copilot_history", [])
    if not hist:
        st.markdown("**Try:**")
        cols = st.columns(3)
        for col, q in zip(cols, ["Predict congestion for all locations", "What is the 60 minute forecast for Airport Road?", "What incidents are active?"]):
            if col.button(q, width="stretch"):
                st.session_state["_copilot_prefill"] = q; st.rerun()
    for m in hist:
        with st.chat_message(m["role"]):
            st.write(m["content"])
            if m.get("tool_results"):
                with st.expander("Backend results used for this answer"):
                    st.json(m["tool_results"], expanded=False)
    prompt = st.chat_input("Ask the Traffic Copilot…") or st.session_state.pop("_copilot_prefill", None)
    if prompt:
        hist.append({"role": "user", "content": prompt})
        with st.spinner("Thinking…"):
            try:
                resp = get_copilot().chat(prompt)
                hist.append({"role": "assistant", "content": resp["answer"], "tool_results": resp.get("tool_results")}); st.session_state["copilot_mode"] = resp.get("mode", "unknown")
            except Exception as exc:
                hist.append({"role": "assistant", "content": f"Sorry — the assistant failed: {exc}"})
        st.rerun()
    if st.session_state.get("copilot_mode"):
        st.caption(f"Last response mode: {st.session_state['copilot_mode']}")
    if hist and st.button("Clear conversation"):
        st.session_state["copilot_history"] = []; st.rerun()
