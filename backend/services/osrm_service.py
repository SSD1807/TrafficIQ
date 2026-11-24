"""Optional OSRM route comparison client; OSRM route ETAs are not live traffic."""
from __future__ import annotations

import httpx


class OSRMService:
    def __init__(self, base_url: str = "https://router.project-osrm.org", client: httpx.Client | None = None, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> dict:
        # OSRM expects longitude,latitude coordinate order.
        coords = f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
        response = self.client.get(
            f"{self.base_url}/route/v1/driving/{coords}",
            params={"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "true"},
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "status": payload.get("code"),
            "source": "OSRM public routing service",
            "is_live_traffic": False,
            "routes": [
                {"distance_km": r["distance"] / 1000, "duration_min": r["duration"] / 60,
                 "geometry": r.get("geometry", {})}
                for r in payload.get("routes", [])
            ],
        }
