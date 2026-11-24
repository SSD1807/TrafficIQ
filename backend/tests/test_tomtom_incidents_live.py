"""OPTIONAL live check against the real TomTom Incident Details API.

Skipped in normal runs. It needs BOTH a configured key and an explicit opt-in so that a developer machine with a key in
backend/.env never spends quota or needs internet by accident:

    set RUN_LIVE_TOMTOM_TESTS=1            (Windows cmd)   |   $env:RUN_LIVE_TOMTOM_TESTS=1   (PowerShell)
    python -m pytest tests/test_tomtom_incidents_live.py -s -v

A pass proves the endpoint, parameters and response parsing work with your key *right now*. An empty incident list is a
valid result (Bhubaneswar may simply have none); the test does not require incidents to exist.
"""
import os
from pathlib import Path

import pytest

if os.environ.get("RUN_LIVE_TOMTOM_TESTS") == "1":   # opted in: read the key from backend/.env like the app does
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TOMTOM_TESTS") != "1" or not os.environ.get("TOMTOM_API_KEY", "").strip(),
    reason="live TomTom test: set RUN_LIVE_TOMTOM_TESTS=1 and TOMTOM_API_KEY (normal tests never use the network)",
)


def test_live_incident_details_roundtrip():
    from services.tomtom_incidents import IncidentConfig, TomTomIncidentService
    service = TomTomIncidentService(config=IncidentConfig.from_env())
    outcome = service.refresh()
    status = service.status()
    assert outcome.fetched, f"live request failed: {status['state']} - {status['message']}"
    assert status["state"] in ("live", "live_empty") and status["is_live"] is True
    for inc in outcome.incidents:
        assert inc["provider_id"] and inc["geometry"]["type"] in ("Point", "LineString")
        assert inc["kind"] in {"accident", "road_closure", "road_work", "obstruction", "hazard", "traffic_jam"}
    print(f"\nLIVE OK: {len(outcome.incidents)} incident(s); state={status['state']}; bbox={status['bbox']}; "
          f"skipped_invalid={status['skipped_invalid_incidents']}")
    for inc in outcome.incidents[:10]:
        print(f" - {inc['category']}: {inc['description']} ({inc['provider_id']})")
