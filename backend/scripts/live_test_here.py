"""LIVE HERE connectivity/coverage test. Run it yourself after putting HERE_API_KEY in backend\\.env.

    cd backend
    python scripts/live_test_here.py            # provider checks
    python scripts/live_test_here.py --routing  # also runs the full OSM + HERE traffic-aware route (needs the cached OSM graph)

It makes ~12 real HERE requests (well inside the free tier), prints what HERE actually returned, writes
outputs/live_test_here_result.json and NEVER prints the API key. Nothing is mocked here: a PASS only means HERE answered.
The verdict about Bhubaneswar coverage is based purely on the segments HERE returns for Bhubaneswar areas.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from services.traffic_providers.here_provider import HereProvider  # noqa: E402

AREAS = {  # ~3 km windows (min_lon, min_lat, max_lon, max_lat) around well-known Bhubaneswar localities
    "Master Canteen / Railway Station": (85.828, 20.255, 85.858, 20.283),
    "Patia / Infocity": (85.810, 20.335, 85.840, 20.362),
    "Old Town / Lingaraj": (85.825, 20.225, 85.855, 20.252),
    "Jaydev Vihar / Nandankanan Rd": (85.815, 20.292, 85.845, 20.318),
    "Airport / Saheed Nagar": (85.800, 20.240, 85.830, 20.265),
}
WHOLE_CITY = (85.74, 20.22, 85.92, 20.37)
ETA_PAIRS = {"Railway Station -> Infocity": ((20.2686, 85.8431), (20.3465, 85.8217)),
             "Airport -> Master Canteen": ((20.2444, 85.8178), (20.2706, 85.8431))}
results: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": {}}


def check(name: str, ok: bool, detail: str = "", **extra):
    results["checks"][name] = {"ok": ok, "detail": detail, **extra}
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def tile_xy(lat: float, lon: float, z: int):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def main() -> int:
    p = HereProvider()
    if not p.configured:
        print("HERE_API_KEY is missing from backend\\.env - add it (see docs/HERE_SETUP.md) and re-run. No requests were made.")
        return 2

    # 1. authentication + live flow for the whole city window
    r = p.fetch_flow(*WHOLE_CITY)
    auth_ok = r.status not in ("unauthorized", "disabled")
    check("1. authentication", auth_ok, r.message or "HERE accepted the key")
    if r.status == "rate_limited":
        print("HERE reports a rate limit / quota problem; stop here and retry later."); return 3
    check("2. live flow retrieval (whole city)", r.status == "ok" and r.data_state == "live",
          f"status={r.status} state={r.data_state} segments={len(r.segments)} sourceUpdated={r.source_updated}")

    # 2. unit / sanity checks on what came back
    if r.segments:
        cur = [s.current_kph for s in r.segments if s.current_kph is not None]
        free = [s.free_flow_kph for s in r.segments if s.free_flow_kph is not None]
        jam = [s.jam_factor for s in r.segments if s.jam_factor is not None]
        plausible = 5 <= statistics.median(free) <= 120 and max(free) <= 200
        check("3. speed units plausible (assumed m/s -> km/h)", plausible,
              f"median free-flow {statistics.median(free):.0f} km/h, median current {statistics.median(cur):.0f} km/h, max free {max(free):.0f} km/h")
        levels = {lv: sum(1 for s in r.segments if s.level == lv) for lv in ("free", "slow", "heavy", "severe", "closed")}
        print("    jam levels:", levels, "| jamFactor min/median/max:", f"{min(jam):.1f}/{statistics.median(jam):.1f}/{max(jam):.1f}")
        results["jam_levels"] = levels
        results["measurements_with_confidence"] = len(r.measurements)
        if r.source_updated:
            print("    HERE sourceUpdated:", r.source_updated, "(compare with the current UTC time to judge freshness)")

    # 3. coverage by locality
    covered = {}
    for label, bbox in AREAS.items():
        a = p.fetch_flow(*bbox)
        covered[label] = len(a.segments)
        print(f"    {label}: {len(a.segments)} segments (status={a.status})")
    results["coverage_segments_by_area"] = covered
    n_cov = sum(1 for v in covered.values() if v > 0)
    verdict = ("CONFIRMED: HERE returned flow segments in every sampled Bhubaneswar area" if n_cov == len(AREAS) else
               f"PARTIAL: segments in {n_cov}/{len(AREAS)} sampled areas" if n_cov else
               "NOT CONFIRMED: HERE returned no flow segments for Bhubaneswar areas")
    results["coverage_verdict"] = verdict
    check("4. traffic coverage around Bhubaneswar", n_cov > 0, verdict)
    print("    NOTE: this proves HERE returns flow data for these areas. Whether jam values reflect LIVE probe data or mostly "
          "historical/free-flow patterns can only be judged by comparing at peak vs night time.")

    # 4. map tile
    x, y = tile_xy(20.2961, 85.8245, 13)
    t = p.tile(13, x, y)
    png = bool(t.content) and t.content[:4] == b"\x89PNG"
    check("5. traffic tile layer (z13 city centre)", t.status == "ok" and png, f"status={t.status} bytes={len(t.content or b'')} png={png}")

    # 5. traffic-aware ETA
    for label, (o, d) in ETA_PAIRS.items():
        e = p.route_eta(o, d).to_public()
        ok = e["status"] == "ok" and e["duration_min"] is not None
        check(f"6. route ETA {label}", ok, f"traffic {e['duration_min']} min vs free-flow {e['free_flow_duration_min']} min (delay {e['traffic_delay_min']} min), {e['length_km']} km" if ok else str(e["message"]))
        results.setdefault("etas", {})[label] = e

    # 6. failure behaviour with an invalid key (a real request that must be rejected cleanly)
    bad = HereProvider(api_key="invalid-key-for-test")
    br = bad.fetch_flow(*AREAS["Patia / Infocity"])
    check("7. invalid key handled cleanly", br.status == "unauthorized" and not br.is_live and br.data_state == "unavailable", f"status={br.status}")

    # 7. optional end-to-end routing with the project's OSM graph
    if "--routing" in sys.argv:
        try:
            from backend.dependencies import get_optimizer
            from routing.traffic_routing import route_with_provider_flow
            opt = get_optimizer()
            o = opt.nearest_node(*ETA_PAIRS["Railway Station -> Infocity"][0]); d = opt.nearest_node(*ETA_PAIRS["Railway Station -> Infocity"][1])
            res = route_with_provider_flow(opt, p, o, d, alternatives=3)
            f = res["traffic_flow"]
            check("8. end-to-end OSM + HERE traffic-aware routing", f["status"] in ("applied", "no_edge_match"),
                  f"status={f['status']} data_state={f['traffic_data_state']} edges_matched={f['edges_matched']} "
                  f"best={res['routes'][0]['estimated_travel_time_min']} min (baseline {f.get('baseline_best_time_min')} min)")
            results["routing"] = {k: f.get(k) for k in ("status", "traffic_data_state", "is_live", "edges_matched", "segments_received", "coverage_best_route", "note")}
        except Exception as exc:  # noqa: BLE001
            check("8. end-to-end OSM + HERE traffic-aware routing", False, f"{type(exc).__name__}: {exc}")

    out = ROOT / "outputs" / "live_test_here_result.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    failed = [k for k, v in results["checks"].items() if not v["ok"]]
    print(f"\nResult file: {out}\n{'ALL CHECKS PASSED' if not failed else 'FAILED: ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
