from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

from ui import maps
from ui import status as S
from ui.context import CENTER, Ctx, hour_bucket, location_anchors, predictions_table, simulated_congestion
from ui.route_helpers import compute_routes, default_departure, named_roads
from ui.theme import is_dark, note, page_header, show_chart


def render(ctx: Ctx) -> None:
    prov = ctx.provider
    page_header("Interactive Traffic Map", "Real OpenStreetMap road geometry with route, congestion-scenario and incident layers",
                badges=[S.road_badge(prov.source)])

    if not prov.is_real_osm:
        st.warning("**Synthetic fallback graph in use** — these are 8 illustrative junctions, not real Bhubaneswar roads. "
                   "Download the OpenStreetMap network below to use actual road geometry.")
    else:
        note("Road geometry, names, types and one-way restrictions are real OpenStreetMap data (static reference). "
             "Colours on roads are a SIMULATED congestion scenario or the OSM road class — OpenStreetMap contains no live traffic.")

    _network_panel(ctx)

    # ------------------------------------------------------------------ controls
    c1, c2, c3, c4 = st.columns([1.1, 1.3, 1.4, 1.2])
    tile = c1.selectbox("Basemap", ["Light", "Dark", "OpenStreetMap"], index=1 if is_dark() else 0)
    mode = c2.radio("Road colouring", ["Road type", "Simulated congestion"], horizontal=False,
                    help="'Simulated congestion' is a deterministic time-of-day scenario model, not measured traffic.")
    detail = c3.radio("Road detail", ["Major roads", "All roads"], horizontal=False,
                      help="Major = motorway to tertiary. 'All roads' can be slow on a city-wide graph.")
    click_mode = c4.radio("Map click sets", ["Nothing", "Origin", "Destination"], horizontal=False)

    with st.expander("Layers and scenario settings", expanded=False):
        a, b, c = st.columns(3)
        show_locs = a.checkbox("Monitored locations (ML prediction)", True)
        show_inc = a.checkbox("Incidents and closures", True)
        dep = default_departure()
        hour = b.slider("Scenario hour (IST)", 0, 23, dep.hour)
        rain = c.slider("Rainfall assumption (mm)", 0.0, 20.0, 0.0, 0.5)
        sim_when = dep.replace(hour=hour, minute=0)

    classes = maps.MAJOR if detail == "Major roads" else maps.ALL_CLASSES
    # Fall back to everything if the chosen classes would leave nothing to draw (tiny graphs).
    if not ctx.edges.highway.isin(classes).any():
        classes = maps.ALL_CLASSES

    congestion = simulated_congestion(ctx.graph_key, hour_bucket(sim_when), float(rain), ctx.graph) if mode == "Simulated congestion" else None

    # ---------------------------------------------------------------- build map
    center, bounds = maps.graph_bounds(ctx.edges)
    if not prov.is_real_osm:
        center = CENTER
    fmap = maps.base_map(center, 12 if prov.is_real_osm else 14, tile)
    with st.spinner("Drawing road network…"):
        drawn = maps.add_roads(fmap, ctx.edges, classes=classes, mode=mode, congestion=congestion)
    if show_locs and prov.is_real_osm:
        preds = predictions_table(ctx.traffic, ctx.traffic.get_current_state()["data_timestamp"])
        locs = preds.dropna(subset=["lat", "lon"]).to_dict("records")
        maps.add_locations(fmap, locs)
    elif show_locs:
        st.caption("Monitored-location markers are hidden on the synthetic fallback graph because their anchors refer to real Bhubaneswar areas.")
    n_inc = maps.add_incidents(fmap, ctx.graph, ctx.incidents.list_active()) if show_inc else 0

    origin, dest = st.session_state.get("map_origin"), st.session_state.get("map_dest")
    result = st.session_state.get("latest_route_result")
    if result and result.get("routes"):
        maps.add_routes(fmap, result["routes"])
    maps.add_endpoints(fmap, origin, dest)
    maps.add_legend(fmap, mode, is_dark(), markers=bool(show_locs and prov.is_real_osm))
    import folium
    folium.LayerControl(collapsed=True, position="topright").add_to(fmap)
    if prov.is_real_osm and not (origin or dest):
        fmap.fit_bounds(bounds)

    out = st_folium(fmap, use_container_width=True, height=640, returned_objects=["last_clicked"], key=f"main_map_{ctx.graph_key}")
    st.caption(f"{drawn:,} road segments drawn · {n_inc} incident markers · source: {prov.source.replace('_', ' ')}"
               + (" · © OpenStreetMap contributors" if prov.is_real_osm else ""))

    # --------------------------------------------------------------- click handling
    click = (out or {}).get("last_clicked")
    if click and click_mode != "Nothing":
        point = (round(click["lat"], 6), round(click["lng"], 6))
        if st.session_state.get("_last_click") != (point, click_mode):
            st.session_state["_last_click"] = (point, click_mode)
            st.session_state["map_origin" if click_mode == "Origin" else "map_dest"] = point
            st.rerun()

    _route_bar(ctx, origin, dest, sim_when, rain)


def _route_bar(ctx: Ctx, origin, dest, sim_when, rain) -> None:
    st.markdown("#### Route from map selection")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Origin:** {origin if origin else 'not set'}")
    c2.markdown(f"**Destination:** {dest if dest else 'not set'}")
    algo = c3.selectbox("Algorithm", ["dijkstra", "astar"], format_func=lambda a: "Dijkstra" if a == "dijkstra" else "A*", key="map_algo")
    b1, b2, _ = st.columns([1, 1, 3])
    go = b1.button("Find routes", type="primary", disabled=not (origin and dest), help="Set both points by clicking the map (choose 'Map click sets').")
    if b2.button("Clear points"):
        for k in ("map_origin", "map_dest", "latest_route_result", "_last_click"):
            st.session_state.pop(k, None)
        st.rerun()
    if go:
        try:
            with st.spinner("Computing routes…"):
                st.session_state["latest_route_result"] = compute_routes(
                    ctx, origin, dest, algorithm=algo, departure=sim_when, rain_mm=rain, use_sim=True, alternatives=3)
            st.rerun()
        except Exception as exc:
            st.error(f"Route calculation failed: {exc}")
    res = st.session_state.get("latest_route_result")
    if res and res.get("routes"):
        rows = [{"Route": "Best" if r["rank"] == 1 else f"Alt {r['rank'] - 1}", "Time (min)": r["estimated_travel_time_min"],
                 "Distance (km)": r["distance_km"], "Delay vs free-flow (min)": r["delay_vs_free_flow_min"],
                 "Via": ", ".join(r.get("via_roads") or []) or "—"} for r in res["routes"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Times use a simulated congestion scenario, not live speeds. Full details on the Route Planner page.")


def _network_panel(ctx: Ctx) -> None:
    prov = ctx.provider
    s = prov.summary()
    with st.expander(f"Road network · {s['nodes']:,} nodes · {s['edges']:,} edges · {s['source'].replace('_', ' ')}", expanded=not prov.is_real_osm):
        left, right = st.columns([2, 3])
        with left:
            st.markdown("**Load real OpenStreetMap roads**")
            st.caption("Downloads the drivable network for Bhubaneswar via OSMnx/Overpass and caches it as GraphML, so it is only needed once. "
                       "It can take a few minutes the first time.")
            if st.button("Download Bhubaneswar road network", type="primary", key="dl_osm"):
                with st.spinner("Contacting OpenStreetMap (Overpass)… this can take several minutes"):
                    try:
                        prov.download()
                        st.success("Road network downloaded and cached."); st.rerun()
                    except Exception as exc:
                        st.error(f"Download failed — the dashboard keeps using the current graph. {exc}")
            if prov.has_cache and prov.source == "synthetic_fallback":
                if st.button("Reload cached graph"):
                    prov.graph = None; prov.load_cached_only(); st.rerun()
            up = st.file_uploader("…or import an offline .osm XML export", type=["osm", "xml"], key="osm_upload",
                                  help="Export from openstreetmap.org (Export tab) or JOSM if Overpass is blocked on your network.")
            if up is not None and st.button("Use uploaded file"):
                with tempfile.NamedTemporaryFile(suffix=".osm", delete=False) as fh:
                    fh.write(up.getvalue())
                try:
                    prov.load_osm_xml(fh.name); st.success("Imported."); st.rerun()
                except Exception as exc:
                    st.error(f"Could not parse the OSM file: {exc}")
            if s.get("last_error") and not prov.is_real_osm:
                st.caption(f"Last status: {s['last_error']}")
        with right:
            if s.get("road_types"):
                df = pd.DataFrame({"Road type": list(s["road_types"]), "Segments": list(s["road_types"].values())}).head(9)
                fig = px.bar(df, x="Segments", y="Road type", orientation="h", title="Road segments by OSM type")
                fig.update_yaxes(autorange="reversed")
                show_chart(fig, 260)
                st.caption(f"{s['named_edges'] / max(1, s['edges']):.0%} of segments carry a road name · {s['oneway_edges']:,} one-way segments"
                           + (f" · query: {s['query']}" if s.get('query') else ""))
