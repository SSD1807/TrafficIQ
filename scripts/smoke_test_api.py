"""Live HTTP smoke test of the running FastAPI backend (standard library only).

    python scripts/smoke_test_api.py [--base http://127.0.0.1:8000] [--origin http://127.0.0.1:5173]

Exercises every endpoint family the React frontend uses, including browser-style CORS pre-flight,
Dijkstra + A*, alternative routes, incident create -> reroute -> delete, weather, Copilot and the
model-evaluation endpoint. External services (Open-Meteo, TomTom, OpenAI, Nominatim) are reported
as 'degraded/unavailable' rather than failed when the machine is offline or keys are not set.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8000")
ap.add_argument("--origin", default="http://127.0.0.1:5173")
args = ap.parse_args()
results = []


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(args.base + path, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            try:
                body_out = json.loads(raw) if raw else None
            except ValueError:  # e.g. CORS pre-flight returns plain text
                body_out = raw.decode(errors="replace")
            return r.status, body_out, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except Exception:
            return e.code, raw.decode(errors="replace"), dict(e.headers)


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


s, h, hd = call("GET", "/health", headers={"Origin": args.origin})
check("health 200 + classifier + forecast artifacts", s == 200 and h["classifier_available"] and h["forecast_artifacts_available"], f"version={h.get('version')}")
check("CORS header for frontend origin", hd.get("access-control-allow-origin") == args.origin, str(hd.get("access-control-allow-origin")))
s, _, hd = call("OPTIONS", "/routes/calculate", headers={"Origin": args.origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
check("CORS pre-flight for POST /routes/calculate", s == 200 and hd.get("access-control-allow-origin") == args.origin)

s, loc, _ = call("GET", "/locations")
locations = loc["locations"]
check("locations (10)", s == 200 and len(locations) == 10)
s, cur, _ = call("GET", "/traffic/current")
check("traffic/current is labelled not-live", s == 200 and cur["is_live"] is False and len(cur["locations"]) == 10)
s, p, _ = call("GET", "/traffic/predictions")
check("traffic/predictions (10, classes valid)", s == 200 and len(p["predictions"]) == 10 and all(x.get("predicted_congestion") in ("Low", "Medium", "High") for x in p["predictions"]))

s, one, _ = call("POST", f"/traffic/predict/{urllib.parse.quote(locations[0])}", {})
check("predict one location (probabilities sum ~1)", s == 200 and abs(sum(one["class_probabilities"].values()) - 1) < 1e-3, one.get("model_name", ""))
for hz in (30, 60):
    for model in ("random_forest", "xgboost"):
        s, f, _ = call("GET", f"/forecasts/{urllib.parse.quote(locations[0])}?horizon_minutes={hz}&model={model}")
        check(f"forecast +{hz}min {model}", s == 200 and f["horizon_minutes"] == hz and f["avg_speed"] is not None, f"speed={f.get('avg_speed') and round(f['avg_speed'], 1)}")

s, g, _ = call("GET", "/routing/nodes")
nodes = g["nodes"]
check("routing/nodes use latitude/longitude", s == 200 and len(nodes) >= 2 and "latitude" in nodes[0] and "lat" not in nodes[0], f"graph_source={g['graph_source']} nodes={len(nodes)}")
o, d = nodes[0]["id"], nodes[min(7, len(nodes) - 1)]["id"]
rs = {}
for alg in ("dijkstra", "astar"):
    s, r, _ = call("POST", "/routes/calculate", {"origin_node": o, "destination_node": d, "algorithm": alg, "alternatives": 3})
    rs[alg] = r
    check(f"route {alg} with alternatives", s == 200 and r["route_count"] >= 1 and r["routes"][0]["polyline_latlon"], f"routes={r.get('route_count')} dist={r['routes'][0]['distance_km']}km")
check("dijkstra and A* agree on best travel time", abs(rs["dijkstra"]["routes"][0]["estimated_travel_time_min"] - rs["astar"]["routes"][0]["estimated_travel_time_min"]) < 0.01)
s, _, _ = call("POST", "/routes/calculate", {"origin_node": o, "destination_node": d, "origin_lat": 20.29, "origin_lon": 85.8, "destination_lat": 20.2, "destination_lon": 85.8})
check("validator rejects two endpoint pairs (422)", s == 422)
n0, n1, n2 = nodes[0], nodes[min(7, len(nodes) - 1)], None
s, rc, _ = call("POST", "/routes/calculate", {"origin_lat": n0["latitude"], "origin_lon": n0["longitude"], "destination_lat": n1["latitude"], "destination_lon": n1["longitude"]})
check("route by coordinates", s == 200 and rc["route_count"] >= 1)

base_seq = rs["dijkstra"]["routes"][0]["node_sequence"]
s, inc, _ = call("POST", "/incidents", {"edge_u": base_seq[0], "edge_v": base_seq[1], "kind": "accident", "severity": 0.9, "bidirectional": True, "description": "smoke test"})
check("incident create (201)", s == 201, str(inc.get("id", ""))[:8])
s, rr, _ = call("POST", "/routes/calculate", {"origin_node": o, "destination_node": d})
check("dynamic rerouting around incident", s == 200 and rr["active_incident_count"] >= 1 and rr["routes"][0]["node_sequence"] != base_seq, f"{base_seq} -> {rr['routes'][0]['node_sequence']}")
s, _, _ = call("DELETE", f"/incidents/{inc['id']}")
check("incident delete (200)", s == 200)
s, _, _ = call("DELETE", f"/incidents/{inc['id']}")
check("incident delete again (404)", s == 404)

s, w, _ = call("GET", "/weather/current")
check("weather endpoint honest status", s == 200 and w["status"] in ("ok", "stale_cache", "unavailable") and w.get("is_simulated") is False, f"status={w['status']}")
s, ws, _ = call("GET", "/weather/current?allow_simulated_fallback=true")
check("weather simulated fallback is labelled", s == 200 and (ws["status"] != "simulated_fallback" or ws["is_simulated"] is True))
s, t, _ = call("GET", "/traffic/tomtom/flow")
check("tomtom endpoint responds (disabled/unavailable without key)", s == 200 and "status" in t, f"status={t.get('status')}")
s, c, _ = call("POST", "/copilot/chat", {"message": "Which locations have predicted traffic congestion?"})
check("copilot answers from tools", s == 200 and "predictions" in c["tool_results"], f"mode={c['mode']}")
s, e, _ = call("GET", "/models/evaluation")
check("models/evaluation (saved artifacts)", s == 200 and e["is_live"] is False and e["classification"] and e["forecasting"])
s, oa, _ = call("GET", "/openapi.json")
check("openapi.json served", s == 200 and "/routes/calculate" in oa["paths"], f"{len(oa['paths'])} paths")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
