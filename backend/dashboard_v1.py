"""Modern Streamlit demonstration dashboard. Original interface preserved in dashboard_legacy.py."""
from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo
import streamlit as st
import folium
from streamlit_folium import st_folium

from backend.dependencies import (
    ROOT, get_copilot, get_forecasts, get_incidents, get_optimizer,
    get_provider, get_traffic, get_weather,
)
from simulation.traffic_simulator import TrafficSimulator

st.set_page_config(page_title="Traffic Intelligence | Bhubaneswar", page_icon="🚦", layout="wide")


def status_label(payload: dict) -> str:
    """Render provenance without conflating stale, synthetic and unavailable data."""
    status = str(payload.get("status", "unknown")).casefold()
    source = payload.get("source", "unknown")
    if status == "stale_cache":
        label = "STALE EXTERNAL CACHE"
    elif status == "simulated_fallback" or payload.get("is_simulated"):
        label = "SIMULATED FALLBACK"
    elif status in {"unavailable", "disabled", "error"}:
        label = "UNAVAILABLE"
    elif payload.get("is_live") and status == "ok":
        label = "LIVE EXTERNAL RESPONSE"
    elif payload.get("is_live"):
        label = "EXTERNAL / NOT CONFIRMED CURRENT"
    else:
        label = "HISTORICAL / SYNTHETIC"
    return f"{label} · {source}"


@st.cache_data(ttl=30)
def load_evaluation(path: str):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else None

traffic = get_traffic()
weather_service = get_weather()
incidents = get_incidents()
provider = get_provider()
# Load once before rendering the overview so the road-source banner is accurate.
# Network download failure is handled by the provider's clearly labelled fallback.
graph = provider.load(allow_fallback=True)

st.title("🚦 Traffic Bottleneck Prediction & Smart Routing")
st.caption("Bhubaneswar demonstration | ML predictions, route optimization, weather and incident simulation")

# A visible source banner avoids showing synthetic/historical inputs as current traffic.
state = traffic.get_current_state()
if state.get("status") == "ok":
    st.warning(f"Traffic source: {state['source']} · timestamp {state['data_timestamp']} · NOT LIVE ROAD TRAFFIC")
else:
    st.error("Synthetic traffic dataset is unavailable. Train or restore the Phase 1 artifacts.")

tabs = st.tabs(["Overview", "Road map & routing", "Predictions & forecasts", "Weather", "Incident lab", "AI Copilot", "Model evaluation", "Legacy simulation"])

with tabs[0]:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Configured locations", len(traffic.list_locations()))
    col2.metric("Road graph nodes", provider.graph.number_of_nodes() if provider.graph is not None else "Not loaded")
    col3.metric("Road graph edges", provider.graph.number_of_edges() if provider.graph is not None else "Not loaded")
    col4.metric("Active incidents", len(incidents.list_active()))
    st.subheader("Data and service provenance")
    st.write({
        "traffic": {"status": "HISTORICAL / SYNTHETIC", "source": "synthetic_bhubaneswar_dataset", "is_live": False},
        "road_network": {"status": "REAL OSM GEOMETRY" if provider.source.startswith("openstreetmap") else "SIMULATED FALLBACK", "source": provider.source, "is_live": False},
        "weather": {"status": "NOT REQUESTED" if "weather_result" not in st.session_state else status_label(st.session_state["weather_result"]), "source": "Open-Meteo (model-derived), or explicit synthetic fallback"},
        "TomTom": {"status": "CONFIGURED — NOT REQUESTED" if os.getenv("TOMTOM_API_KEY") else "UNAVAILABLE — API KEY NOT CONFIGURED", "source": "TomTom Traffic Flow / Incidents"},
        "copilot": {"status": "OPENAI CONFIGURED — NOT REQUESTED" if os.getenv("OPENAI_API_KEY") else "RULE-BASED FALLBACK", "source": "OpenAI Responses API or local rules"},
    })
    if provider.source == "synthetic_fallback":
        st.warning(f"Road graph is a SYNTHETIC FALLBACK, not OpenStreetMap. Provider detail: {provider.last_error or 'OSM graph not available'}")
    if state.get("locations"):
        st.dataframe(state["locations"], use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader("Origin and destination")
    optimizer = get_optimizer()
    node_options = list(graph.nodes)
    def node_label(node):
        attrs = graph.nodes[node]
        return f"{attrs.get('name', 'Road node')} · {node}"
    labels = {node_label(node): node for node in node_options}
    c1, c2, c3 = st.columns(3)
    origin_label = c1.selectbox("Origin road node", list(labels), index=0, key="route_origin")
    dest_idx = min(1, max(0, len(labels) - 1))
    destination_label = c2.selectbox("Destination road node", list(labels), index=dest_idx, key="route_destination")
    algorithm = c3.selectbox("Algorithm", ["dijkstra", "astar"])
    use_sim = st.checkbox("Apply simulated congestion costs", value=True)
    rain_for_route = st.slider("Rainfall assumption (mm)", 0.0, 20.0, 0.0, 0.5)
    departure_col1, departure_col2 = st.columns(2)
    departure_date = departure_col1.date_input("Departure date", value=date.today())
    departure_time = departure_col2.time_input("Departure time (India time)", value=datetime.now(ZoneInfo("Asia/Kolkata")).time().replace(second=0, microsecond=0))
    departure_dt = datetime.combine(departure_date, departure_time, tzinfo=ZoneInfo("Asia/Kolkata"))
    route_clicked = st.button("Calculate best and alternative routes", type="primary")
    if route_clicked:
        origin_node, destination_node = labels[origin_label], labels[destination_label]
        try:
            congestion = TrafficSimulator().edge_conditions(graph, departure_dt, rainfall_mm=rain_for_route) if use_sim else None
            result = optimizer.route_nodes(origin_node, destination_node, algorithm=algorithm,
                                           alternatives=3, congestion_by_edge=congestion,
                                           rainfall_mm=rain_for_route)
            result["departure_time"] = departure_dt.isoformat()
            result["estimated_arrival_time"] = (departure_dt + __import__("datetime").timedelta(minutes=result["routes"][0]["estimated_travel_time_min"])).isoformat() if result.get("routes") else None
            st.session_state["latest_route_result"] = result
        except Exception as exc:
            st.error(f"Route calculation failed: {exc}")
    result = st.session_state.get("latest_route_result")
    if result:
        st.caption(f"Graph source: {result['graph_source']} · Real OSM geometry: {result['routes'][0]['is_real_road_geometry']}")
        if result["routes"]:
            cols = st.columns(len(result["routes"]))
            for i, route in enumerate(result["routes"]):
                cols[i].metric(f"Route {i+1}", f"{route['estimated_travel_time_min']:.1f} min", f"{route['distance_km']:.2f} km")
            center = [float(graph.nodes[labels[origin_label]].get("y", 20.2961)), float(graph.nodes[labels[origin_label]].get("x", 85.8245))]
            fmap = folium.Map(location=center, zoom_start=13, control_scale=True)
            route_colors = ["#16803c", "#2563eb", "#9333ea", "#ea580c", "#0f766e"]
            for i, route in enumerate(result["routes"]):
                if route["polyline_latlon"]:
                    folium.PolyLine(route["polyline_latlon"], color=route_colors[i % len(route_colors)],
                                    weight=6 if i == 0 else 4, opacity=0.9,
                                    tooltip=f"Route {i+1}: {route['estimated_travel_time_min']:.1f} min").add_to(fmap)
            folium.Marker(center, tooltip="Origin", icon=folium.Icon(color="green", icon="play")).add_to(fmap)
            if result["routes"][0]["polyline_latlon"]:
                folium.Marker(result["routes"][0]["polyline_latlon"][-1], tooltip="Destination",
                              icon=folium.Icon(color="red", icon="flag")).add_to(fmap)
            st_folium(fmap, use_container_width=True, height=480)
            st.dataframe([{k: v for k, v in r.items() if k in ["rank", "algorithm", "distance_km", "estimated_travel_time_min", "free_flow_estimate_min", "delay_vs_free_flow_min"]} for r in result["routes"]], use_container_width=True, hide_index=True)
            st.caption(f"Departure: {result.get('departure_time')} · Estimated arrival: {result.get('estimated_arrival_time')}")
            st.info("OSM provides static road geometry/attributes. Simulated congestion modifies route cost only when enabled; these values are not real traffic speeds.")
            st.caption("This routing engine applies Low 1.00×, Medium 1.25× and High 1.75× travel-time multipliers. Rain adds up to 36% at 30 mm. Accidents apply (1 + 2 × severity) plus 30–210 seconds; roadworks/obstructions/hazards apply (1 + 0.8 × severity) plus 15–105 seconds. A road closure removes the directed edge from the route graph. These are simulation assumptions, not measured traffic penalties.")

with tabs[2]:
    locations = traffic.list_locations()
    if locations:
        location = st.selectbox("Location", locations, key="forecast_location")
        horizon = st.radio("Forecast horizon", [30, 60], horizontal=True)
        if st.button("Run congestion prediction and numeric forecast"):
            try:
                st.session_state["classification_prediction"] = traffic.predict_next(location)
                st.session_state["numeric_forecast"] = get_forecasts().forecast(location, horizon)
            except Exception as exc:
                st.error(str(exc))
        if "classification_prediction" in st.session_state:
            p = st.session_state["classification_prediction"]
            st.metric("Next 30-minute congestion class", p["predicted_congestion"])
            if p.get("class_probabilities"):
                st.bar_chart(p["class_probabilities"])
            st.caption(f"Classifier: {p.get('model_name', 'saved model')} · {p['notice']}")
        if "numeric_forecast" in st.session_state:
            f = st.session_state["numeric_forecast"]
            a, b, c = st.columns(3)
            a.metric("Vehicle count forecast", f"{f['vehicle_count']:.0f}" if f.get("vehicle_count") is not None else "N/A")
            b.metric("Average speed forecast", f"{f['avg_speed']:.1f}" if f.get("avg_speed") is not None else "N/A")
            c.metric("Congestion forecast", f.get("predicted_congestion") or "N/A")
            st.caption(f["notice"])
        forecast_metrics = load_evaluation(str(ROOT / "outputs" / "forecasting" / "forecast_metrics.json"))
        if forecast_metrics:
            st.subheader("Held-out forecast benchmark")
            st.json(forecast_metrics["results"], expanded=False)
            for horizon in (30, 60):
                st.markdown(f"**{horizon}-minute actual vs predicted traffic forecasts**")
                columns = st.columns(2)
                for col, target in zip(columns, ("vehicle_count", "avg_speed")):
                    chart_path = ROOT / "outputs" / "forecasting" / f"forecast_comparison_{horizon}m_{target}.png"
                    if chart_path.exists():
                        col.image(str(chart_path), caption=f"{horizon}m · {target.replace('_', ' ')}", use_container_width=True)
        else:
            st.info("Run `python -m forecasting.forecast_pipeline` to train the 30/60-minute regression and class-forecast models.")

with tabs[3]:
    st.subheader("Bhubaneswar current weather")
    allow_fallback = st.checkbox("Allow explicitly labelled simulated fallback if Open-Meteo is unavailable", value=False)
    if st.button("Fetch Open-Meteo conditions"):
        st.session_state["weather_result"] = weather_service.get_current(allow_simulated_fallback=allow_fallback, force_refresh=True)
    weather = st.session_state.get("weather_result")
    if weather:
        st.markdown(f"**Source:** {status_label(weather)}")
        if weather.get("status") in ["ok", "stale_cache", "simulated_fallback"]:
            cols = st.columns(4)
            cols[0].metric("Temperature", f"{weather.get('temperature_c')} °C")
            cols[1].metric("Rain", f"{weather.get('rain_mm')} mm")
            cols[2].metric("Wind", f"{weather.get('wind_speed_kmh')} km/h")
            cols[3].metric("Cloud cover", f"{weather.get('cloud_cover_pct')} %")
            st.caption(weather.get("notice") or weather.get("attribution"))
        else:
            st.error(weather.get("message", weather.get("error", "Weather unavailable")))
    else:
        st.caption("No API call has been made yet. Weather is not inferred or fabricated in this panel.")

with tabs[4]:
    st.subheader("Incident and road-closure simulation")
    edge_options = list(graph.edges())
    edge_labels = {f"{graph.nodes[u].get('name', u)} → {graph.nodes[v].get('name', v)} [{u}→{v}]": (u, v) for u, v in edge_options[:500]}
    if edge_labels:
        edge_label = st.selectbox("Affected directed road segment", list(edge_labels), key="incident_edge")
        kind = st.selectbox("Incident type", ["accident", "road_closure", "road_work", "obstruction", "hazard"])
        severity = st.slider("Severity", 0.0, 1.0, 0.65, 0.05)
        ttl = st.number_input("Expires after (seconds)", 60, 604800, 3600, 60)
        bidir = st.checkbox("Apply to reverse direction as well", value=False)
        if st.button("Add incident to simulation"):
            u, v = edge_labels[edge_label]
            item = incidents.add(u, v, kind=kind, severity=severity, source="simulation",
                                 expires_in_seconds=int(ttl), bidirectional=bidir,
                                 description="Created in Streamlit Incident Lab")
            st.success(f"Incident created: {item['id']}")
    active = incidents.list_active()
    st.write(f"Active incidents: {len(active)}")
    for item in active:
        with st.expander(f"{item['kind']} · {item['edge_u']} → {item['edge_v']} · {item['source']}"):
            st.json(item)
            if st.button("Remove incident", key=f"remove_{item['id']}"):
                incidents.remove(item["id"]); st.rerun()
    st.caption("Road closures remove the directed edge from route search. Accidents, roadworks and hazards increase route travel-time costs according to severity. Incidents expire automatically.")

with tabs[5]:
    st.subheader("AI Traffic Copilot")
    st.caption("OpenAI tool-calling is enabled only when OPENAI_API_KEY is configured. Otherwise the rule-based assistant uses the same backend services.")
    for item in st.session_state.get("copilot_history", []):
        with st.chat_message(item["role"]): st.write(item["content"])
    prompt = st.chat_input("Ask about congestion, weather, incidents, forecasts or routing…")
    if prompt:
        st.session_state.setdefault("copilot_history", []).append({"role": "user", "content": prompt})
        response = get_copilot().chat(prompt)
        st.session_state["copilot_last_mode"] = response.get("mode", "unknown")
        st.session_state["copilot_history"].append({"role": "assistant", "content": response["answer"]})
        st.rerun()
    if st.session_state.get("copilot_last_mode"):
        st.caption(f"Assistant execution mode: {st.session_state['copilot_last_mode']}")
    if st.session_state.get("copilot_history") and st.button("Clear conversation"):
        st.session_state["copilot_history"] = []; st.rerun()

with tabs[6]:
    st.subheader("Phase 1 congestion classifier")
    phase1 = load_evaluation(str(ROOT / "outputs" / "phase1_metrics.json"))
    if phase1:
        data = phase1["holdout_test_metrics"]
        rows = []
        for model, m in data.items():
            rows.append({"Model": model, "Accuracy": m["accuracy"], "Macro F1": m["f1_macro"],
                         "Weighted F1": m["f1_weighted"], "High precision": m["high_class_error_analysis"]["precision"],
                         "High recall": m["high_class_error_analysis"]["recall"],
                         "High FP": m["high_class_error_analysis"]["false_positives"],
                         "High FN": m["high_class_error_analysis"]["false_negatives"]})
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.warning("All Phase 1 metrics are measured on a synthetic Bhubaneswar dataset and do not establish real-world performance.")
    else:
        st.info("Run `python phase2_train.py` to generate the Phase 1 evaluation report.")

with tabs[7]:
    st.subheader("Original interactive simulation")
    st.write("The original dashboard source has been preserved as `dashboard_legacy.py` together with `animation_component.py` and `simulation_engine.py`.")
    st.code("streamlit run dashboard_legacy.py", language="bash")
