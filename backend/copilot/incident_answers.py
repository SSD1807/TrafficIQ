"""Deterministic, data-grounded wording for incident questions.

Every sentence is built from tool results. Nothing here can invent an incident, and simulated/manual incidents are
always labelled as test data - never as live reports.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

INCIDENT_TERMS = ("incident", "accident", "crash", "collision", "closure", "road closed", "roads closed", "closed road",
                  "currently closed", "are closed", "is closed", "roadwork", "road work", "road works", "blocked road")


def is_incident_query(message: str) -> bool:
    msg = message.casefold()
    return any(t in msg for t in INCIDENT_TERMS)


def is_route_context_query(message: str) -> bool:
    msg = message.casefold()
    return any(t in msg for t in ("my route", "selected route", "my selected", "this route", "that route", "alternative route",
                                  "alternate route", "another route", "reroute", "re-route", "detour"))


def focus_of(message: str) -> str:
    msg = message.casefold()
    if any(t in msg for t in ("closed", "closure", "closures")):
        return "closures"
    if any(t in msg for t in ("accident", "crash", "collision")):
        return "accidents"
    return "all"


def _when(iso: str | None) -> str:
    if not iso:
        return "unknown time"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b %H:%M")
    except ValueError:
        return iso


def _place(e: dict[str, Any]) -> str:
    prov = e.get("provider") or {}
    roads = [x for x in dict.fromkeys(ed.get("road_name") for ed in e.get("edges", [])) if x]
    parts = []
    if roads:
        parts.append(", ".join(roads[:2]))
    if prov.get("from") and prov.get("to"):
        parts.append(f"between {prov['from']} and {prov['to']}")
    elif prov.get("from"):
        parts.append(f"near {prov['from']}")
    if prov.get("road_numbers"):
        parts.append("(" + "/".join(prov["road_numbers"]) + ")")
    if not parts and e.get("latitude") is not None:
        parts.append(f"around {e['latitude']:.4f}, {e['longitude']:.4f}")
    return " ".join(parts) or "location not specified"


def _line(e: dict[str, Any]) -> str:
    sev = ""
    prov = e.get("provider") or {}
    if prov.get("magnitude_label") and prov["magnitude_label"] not in ("unknown",):
        sev = f", delay magnitude {prov['magnitude_label']}"
    status = {"applied": "applied to routing", "unmatched": "not matched to a road, not applied",
              "matched_not_applied": "matched but not applied to routing", "stale": "stale, not applied"}.get(e["status"], e["status"])
    return f"- {e['kind'].replace('_', ' ')}: {e['description']} - {_place(e)}{sev} [{status}]"


def provider_sentence(p: dict[str, Any]) -> str:
    state = p["state"]
    if state == "disabled_no_key":
        return ("Live TomTom incident detection is not configured (no TOMTOM_API_KEY in backend/.env), "
                "so I cannot report real-world incidents.")
    if state == "disabled":
        return "Live TomTom incident detection is switched off (TOMTOM_INCIDENTS_ENABLED=false)."
    if state == "unavailable":
        return f"The TomTom incident service is temporarily unavailable ({p['message']}); I have no live incident data to report."
    if state == "idle":
        return "No TomTom incident request has completed yet, so there is no live incident data."
    if state == "cached":
        return (f"The TomTom incident service could not be refreshed, so this is CACHED data from {_when(p.get('last_success_at'))} "
                f"(not live{'; it is stale and not applied to routing' if p.get('stale') else ''}).")
    if state == "live_empty":
        return f"TomTom is live (last updated {_when(p.get('last_success_at'))}) and currently reports no incidents in the Bhubaneswar area."
    return f"TomTom live data (last updated {_when(p.get('last_success_at'))})."


def describe_incidents(tools: dict[str, Any], focus: str = "all") -> str:
    p, feed = tools["provider"], tools["incidents"]
    tomtom = [e for e in feed if e["source"] == "tomtom"]
    local = [e for e in feed if e["source"] != "tomtom"]
    if focus == "closures":
        tomtom = [e for e in tomtom if e["kind"] == "road_closure"]
        local = [e for e in local if e["kind"] == "road_closure"]
    elif focus == "accidents":
        tomtom = [e for e in tomtom if e["kind"] == "accident"]
        local = [e for e in local if e["kind"] == "accident"]
    label = {"closures": "road closure", "accidents": "accident", "all": "incident"}[focus]
    out = [provider_sentence(p)]
    usable = p["state"] in ("live", "live_empty", "cached")
    if usable and tomtom:
        out.append(f"{len(tomtom)} TomTom {label}(s):")
        out += [_line(e) for e in tomtom[:8]]
        if len(tomtom) > 8:
            out.append(f"...and {len(tomtom) - 8} more (see the Incident Management page).")
    elif usable and p["state"] != "cached":
        out.append(f"TomTom reports no {label}s right now.")
    if local:
        out.append(f"Separately, this application session has {len(local)} manual/simulated {label}(s) - test data, NOT live reports:")
        out += [f"- {e['kind'].replace('_', ' ')} ({e['source']}): {e['description'] or 'no description'}" for e in local[:5]]
    return "\n".join(out)


def describe_route_impact(origin: str, destination: str, impact: dict[str, Any], provider: dict[str, Any]) -> str:
    out = [provider_sentence(provider), f"Route {origin} -> {destination}: {impact['reason']}"]
    on_orig, on_new = impact["incidents_on_original_route"], impact["incidents_on_new_route"]
    if on_orig:
        out.append("Incidents on the route that is fastest without live incidents:")
        out += [_line(e) for e in on_orig[:6]]
    base = impact["without_live_incidents"]["routes"][0]
    new = (impact["with_incidents"].get("routes") or [None])[0]
    if new is None:
        out.append(impact["with_incidents"].get("error", "No route available."))
    elif impact["rerouted"]:
        d = impact["delta"]
        out.append(f"Alternative route: {new['distance_km']:.2f} km, about {new['estimated_travel_time_min']:.1f} min "
                   f"(original {base['distance_km']:.2f} km, {base['estimated_travel_time_min']:.1f} min; "
                   f"{d['travel_time_min']:+.1f} min, {d['distance_km']:+.2f} km).")
    elif on_new:
        out.append("No cheaper alternative exists, so the route stays the same and absorbs the incident delay.")
    return "\n".join(out)
