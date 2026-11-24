from __future__ import annotations

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
import folium

from ui import maps
from ui import status as S
from ui.context import Ctx, hour_bucket, location_anchors, simulated_congestion
from ui.route_helpers import compare_algorithms, compute_routes, default_departure, named_roads
from ui.theme import CONGESTION_COLORS, card, is_dark, note, page_header, style_fig


def _endpoint_picker(ctx: Ctx, label: str, key: str, roads: pd.DataFrame, default_index: int = 0) -> tuple[float, float] | None:
    """Pick a point from: monitored location anchor, OSM road name, or the map-click selection."""
    opts = ["Monitored location", "Search OSM road name", "Point picked on map", "Latitude / longitude"]
    mode = st.radio(label, opts, key=f"{key}_mode", horizontal=True, label_visibility="visible")
    if mode == "Monitored location":
        anchors = location_anchors()
        pick = st.selectbox("Location", list(anchors), index=min(default_index, len(anchors) - 1), key=f"{key}_loc", label_visibility="collapsed")
        a = anchors[pick]; st.caption(f"{a['area']} · approximate anchor")
        return (a["lat"], a["lon"])
    if mode == "Search OSM road name":
        if roads.empty:
            st.info("No road names available (synthetic fallback graph). Download the OSM network first.")
            return None
        q = st.text_input("Road name contains", key=f"{key}_q", placeholder="e.g. Janpath, Cuttack Road, NH 16")
        hits = roads[roads.name.str.contains(q, case=False, regex=False)].head(40) if q else roads.head(40)
        if hits.empty:
            st.caption("No matching road names."); return None
        pick = st.selectbox("Road", hits.name.tolist(), key=f"{key}_road", label_visibility="collapsed",
                            format_func=lambda n: f"{n}  ({hits[hits.name == n].highway.iat[0]}, {hits[hits.name == n].km.iat[0]:.1f} km)")
        row = hits[hits.name == pick].iloc[0]
        return (float(row.lat), float(row.lon))
    if mode == "Point picked on map":
        pt = st.session_state.get("map_origin" if key == "orig" else "map_dest")
        if not pt:
            st.caption("Nothing picked yet — use the Traffic Map page (Map click sets → Origin/Destination)."); return None
        st.caption(f"Picked point: {pt}"); return pt
    c1, c2 = st.columns(2)
    lat = c1.number_input("Latitude", 19.9, 20.6, 20.2961, 0.0001, format="%.5f", key=f"{key}_lat")
    lon = c2.number_input("Longitude", 85.5, 86.1, 85.8245, 0.0001, format="%.5f", key=f"{key}_lon")
    return (lat, lon)


def render(ctx: Ctx) -> None:
    prov = ctx.provider
    page_header("Smart Route Optimization", "Fastest route and alternatives with congestion-, weather- and incident-aware travel times",
                badges=[S.road_badge(prov.source), S.SIMULATED])
    if not prov.is_real_osm:
        st.warning("Routing on the **synthetic fallback graph** (8 illustrative junctions). Download the OSM network on the Traffic Map page for real roads.")
    roads = named_roads(ctx)

    a, b = st.columns(2)
    with a: origin = _endpoint_picker(ctx, "Origin", "orig", roads)
    with b: dest = _endpoint_picker(ctx, "Destination", "dest", roads, default_index=6)
    s1, s2, s3, s4 = st.columns(4)
    algo = s1.selectbox("Algorithm", ["dijkstra", "astar"], format_func=lambda a: "Dijkstra" if a == "dijkstra" else "A*")
    alts = s2.slider("Routes to show", 1, 5, 3)
    dep0 = default_departure()
    d_date = s3.date_input("Departure date", dep0.date()); d_time = s3.time_input("Departure time (IST)", dep0.time())
    rain = s4.slider("Rainfall (mm)", 0.0, 20.0, 0.0, 0.5)
    use_sim = st.checkbox("Apply simulated time-of-day congestion", True,
                          help="Deterministic scenario model: rush hours 07–10 and 17–20, weekend and night reductions, rain uplift.")
    has_key = bool(getattr(ctx.tomtom, "api_key", None))
    use_tomtom = st.checkbox("Use TomTom measured flow along the route corridor (experimental)", False, disabled=not has_key,
                             help=("Probes TomTom at points along the candidate routes (max 24 calls), matches each returned road segment to directed OSM edges "
                                   "by position and travel direction, and re-runs the routing algorithm with the measured speeds on matched edges only."
                                   if has_key else "Set TOMTOM_API_KEY in .env to enable. Without it, routes use modelled costs only."))
    if not has_key:
        st.caption("TomTom flow is unavailable (no TOMTOM_API_KEY). Travel times below are modelled, not measured.")
    go = st.button("Find best route", type="primary")

    if go:
        if not (origin and dest):
            st.error("Choose both an origin and a destination.")
        else:
            dep = dep0.replace(year=d_date.year, month=d_date.month, day=d_date.day, hour=d_time.hour, minute=d_time.minute)
            try:
                with st.spinner("Computing routes…"):
                    res = compute_routes(ctx, origin, dest, algorithm=algo, departure=dep, rain_mm=rain, use_sim=use_sim, alternatives=alts, use_tomtom=use_tomtom)
                    res["_compare"] = compare_algorithms(ctx, origin, dest, departure=dep, rain_mm=rain, use_sim=use_sim)
                st.session_state["latest_route_result"] = res
                st.session_state["map_origin"], st.session_state["map_dest"] = origin, dest
            except Exception as exc:
                msg = str(exc)
                if "same road node" in msg:
                    msg = "Origin and destination snap to the same road junction. Choose points that are further apart."
                st.error(f"Route calculation failed: {msg}  If the points are far apart, an active road closure may be disconnecting the network.")

    res = st.session_state.get("latest_route_result")
    if not res or not res.get("routes"):
        st.info("Choose an origin and destination, then press **Find best route**."); return

    routes = res["routes"]; best = routes[0]
    dep = pd.Timestamp(res["departure_time"]); arr = pd.Timestamp(res["estimated_arrival_time"])
    st.markdown(f"##### Result · {len(routes)} route(s) · {res['algorithm'].upper() if res['algorithm']=='astar' else 'Dijkstra'}")
    c = st.columns(5)
    flow = res.get("traffic_flow") or {}
    if flow.get("status") == "applied":
        basis = card("Traffic basis", "Measured + model", f"TomTom on {flow['edges_matched']} directed edges · {flow['coverage_best_route']:.0%} of best route", S.LIVE)
    elif flow.get("status") in {"unavailable", "no_edge_match", "disabled"}:
        basis = card("Traffic basis", "Model only", flow.get("note", "TomTom not applied")[:90], S.UNAVAIL)
    else:
        basis = card("Traffic basis", "Simulated" if res["used_simulated_congestion"] else "Free flow", "No measured traffic used", S.SIMULATED if res["used_simulated_congestion"] else None)
    c[4].markdown(basis, unsafe_allow_html=True)
    c[0].markdown(card("Best route", f"{best['estimated_travel_time_min']:.1f} min", f"{best['distance_km']:.2f} km", S.SIMULATED if res["used_simulated_congestion"] else None), unsafe_allow_html=True)
    c[1].markdown(card("Arrival (est.)", f"{arr:%H:%M}", f"Departure {dep:%d %b %H:%M} IST"), unsafe_allow_html=True)
    c[2].markdown(card("Delay vs free flow", f"+{best['delay_vs_free_flow_min']:.1f} min", f"Free flow {best['free_flow_estimate_min']:.1f} min"), unsafe_allow_html=True)
    c[3].markdown(card("Active incidents", str(res["active_incident_count"]), "Affect route cost / closures"), unsafe_allow_html=True)
    if res["origin_snap_m"] > 300 or res["dest_snap_m"] > 300:
        st.caption(f"Points were snapped to the nearest road node ({res['origin_snap_m']:.0f} m / {res['dest_snap_m']:.0f} m away).")

    center = ((res["origin_latlon"][0] + res["dest_latlon"][0]) / 2, (res["origin_latlon"][1] + res["dest_latlon"][1]) / 2)
    fmap = maps.base_map(center, 13, "Dark" if is_dark() else "Light")
    pts = [p for r in routes for p in r["polyline_latlon"]]
    maps.add_roads(fmap, ctx.edges, classes=maps.MAJOR if len(ctx.edges) > 3000 else maps.ALL_CLASSES, mode="Road type")
    maps.add_incidents(fmap, ctx.graph, ctx.incidents.list_active())
    maps.add_routes(fmap, routes); maps.add_endpoints(fmap, res["origin_latlon"], res["dest_latlon"])
    maps.add_legend(fmap, "Road type", is_dark(), markers=False, compact=True)
    if pts:
        fmap.fit_bounds([[min(p[0] for p in pts), min(p[1] for p in pts)], [max(p[0] for p in pts), max(p[1] for p in pts)]])
    st_folium(fmap, use_container_width=True, height=470, returned_objects=[], key=f"route_map_{res['departure_time']}_{len(routes)}_{best['distance_km']}")
    t1, t2 = st.columns([2, 3])
    with t1:
        rows = [{"Route": "Best" if r["rank"] == 1 else f"Alt {r['rank'] - 1}", "Time (min)": r["estimated_travel_time_min"],
                 "km": r["distance_km"], "Delay (min)": r["delay_vs_free_flow_min"], "Via": ", ".join(r.get("via_roads") or []) or "—"} for r in routes]
        st.markdown("**Route comparison**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with t2:
        pick = st.selectbox("Turn-by-turn for", [f"{'Best route' if r['rank'] == 1 else 'Alternative ' + str(r['rank'] - 1)}" for r in routes])
        r = routes[0 if pick == "Best route" else int(pick.split()[-1])]
        steps = pd.DataFrame(r["steps"]).rename(columns={"road": "Road", "highway": "Type", "distance_m": "Distance (m)", "time_min": "Time (min)", "congestion": "Sim. congestion", "oneway": "One-way"})
        st.dataframe(steps[["Road", "Type", "Distance (m)", "Time (min)", "Sim. congestion", "One-way"]], hide_index=True, width="stretch")
    with st.expander("Algorithm comparison"):
        st.dataframe(pd.DataFrame(res.get("_compare", [])), hide_index=True, width="stretch")
        st.caption("Dijkstra and A* optimise the same cost function, so travel times match; A* may explore fewer nodes on large graphs.")
    if flow.get("status") in {"applied", "unavailable", "no_edge_match"}:
        with st.expander("TomTom flow details"):
            st.json({k: v for k, v in flow.items() if k != "baseline_best_nodes"}, expanded=False)
            st.caption("Only edges matched to a TomTom segment (position + direction) use measured speeds; probing covers the baseline route corridor, so off-corridor roads keep modelled costs.")
    note("OpenStreetMap supplies road geometry and attributes only. Travel times use simulated congestion multipliers (Low 1.00×, Medium 1.25×, High 1.75×), "
         "rain up to +36% at 30 mm, and incident penalties; closures remove the directed edge. These are modelling assumptions, not measured traffic.")
