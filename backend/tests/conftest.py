"""Hermetic test configuration.

backend/.env (loaded by backend.dependencies at import) may hold a real TOMTOM_API_KEY on a developer machine.
Normal tests must never use it or reach the internet, so the key is blanked BEFORE any test module imports the app.
Live checks live in tests/test_tomtom_incidents_live.py and need an explicit opt-in (RUN_LIVE_TOMTOM_TESTS=1).
"""
import os

_LIVE = os.environ.get("RUN_LIVE_TOMTOM_TESTS") == "1"
if not _LIVE:
    # An empty string counts as "already set", so python-dotenv will not overwrite it from backend/.env.
    os.environ["TOMTOM_API_KEY"] = ""
    os.environ["TOMTOM_INCIDENTS_BACKGROUND_POLL"] = "false"
