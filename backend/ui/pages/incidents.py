from __future__ import annotations

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from ui import maps
from ui import status as S
from ui.context import Ctx
from ui.theme import is_dark, note, page_header


def render(ctx: Ctx) -> None:
    page_header("Incident & Closure Lab", "Create simulated accidents, roadworks and closures and see them change routing",
                badges=[S.SYNTH])
    g = ctx.graph
    names = sorted({n for n in ctx.edges.name if n})
    if not ctx.provider.is_real_osm:
        st.caption("Using the synthetic fallback graph; segments are identified by junction names.")
    left, right = st.columns([2, 3])
    with left:
        st.markdown("**Add incident**")
        if names:
            road = st.selectbox("Road", names, help="Names come from OpenStreetMap.")
            seg = ctx.edges[ctx.edges.name == road].reset_index(drop=True)
            idx = st.selectbox("Segment", range(len(seg)), format_func=lambda i: f"{seg.length_m[i]:.0f} m · {seg.highway[i]}{' · one-way' if seg.oneway[i] else ''} · {seg.u[i]}→{seg.v[i]}")
            u, v = seg.u[idx], seg.v[idx]
        else:
            lab = {f"{g.nodes[a].get('name', a)} → {g.nodes[b].get('name', b)}": (a, b) for a, b in list(g.edges())[:500]}
            u, v = lab[st.selectbox("Directed segment", list(lab))]
        kind = st.selectbox("Type", ["accident", "road_closure", "road_work", "obstruction", "hazard"], format_func=lambda k: k.replace("_", " ").title())
        sev = st.slider("Severity", 0.0, 1.0, 0.65, 0.05)
        ttl = st.number_input("Expires after (minutes)", 1, 10080, 60, 5)
        both = st.checkbox("Also apply to reverse direction")
        desc = st.text_input("Note", "Created in Incident Lab")
        if st.button("Add incident", type="primary"):
            item = ctx.incidents.add(u, v, kind=kind, severity=sev, source="simulation", expires_in_seconds=int(ttl) * 60, bidirectional=both, description=desc)
            st.success(f"Added {item['kind'].replace('_', ' ')}."); st.rerun()
    active = ctx.incidents.list_active()
    with right:
        center, bounds = maps.graph_bounds(ctx.edges)
        fmap = maps.base_map(center, 13, "Dark" if is_dark() else "Light")
        maps.add_roads(fmap, ctx.edges, classes=maps.MAJOR if len(ctx.edges) > 3000 else maps.ALL_CLASSES, mode="Road type")
        maps.add_incidents(fmap, g, active)
        fmap.fit_bounds(bounds)
        st_folium(fmap, use_container_width=True, height=430, returned_objects=[], key=f"inc_map_{len(active)}_{ctx.graph_key}")
    st.markdown(f"**Active incidents: {len(active)}**")
    if not active:
        st.caption("No active incidents. Incidents are held in memory and expire automatically.")
    for it in active:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{it['kind'].replace('_', ' ').title()}** · severity {it['severity']:.2f} · {it['edge_u']}→{it['edge_v']} · {it['source']} · expires {it['expires_at'][:16]} UTC")
        if c2.button("Remove", key=f"rm_{it['id']}"):
            ctx.incidents.remove(it["id"]); st.rerun()
    note("Closures remove the directed edge from route search. Accidents, roadworks and hazards raise travel-time costs with severity. All incidents here are simulated or manually entered — not live reports.")
