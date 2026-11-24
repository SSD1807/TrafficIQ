"""Open-Meteo current-condition adapter with explicit cache/fallback provenance."""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Any

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
BHUBANESWAR = {"latitude": 20.2961, "longitude": 85.8245, "timezone": "Asia/Kolkata"}


class WeatherService:
    def __init__(self, client: httpx.Client | None = None, cache_ttl_seconds: int = 600, timeout_seconds: float = 8.0):
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self.timeout_seconds = timeout_seconds

    def _fetch(self) -> dict[str, Any]:
        params = {
            **BHUBANESWAR,
            "current": "temperature_2m,relative_humidity_2m,precipitation,rain,showers,weather_code,cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }
        response = self.client.get(OPEN_METEO_URL, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        current = payload.get("current")
        if not isinstance(current, dict) or not current.get("time"):
            raise ValueError("Open-Meteo response did not include a valid current block")
        return {
            "status": "ok",
            "source": "open_meteo_current_model",
            "is_live": True,
            "is_simulated": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "observation_time": current.get("time"),
            "timezone": payload.get("timezone", BHUBANESWAR["timezone"]),
            "latitude": payload.get("latitude", BHUBANESWAR["latitude"]),
            "longitude": payload.get("longitude", BHUBANESWAR["longitude"]),
            "temperature_c": current.get("temperature_2m"),
            "relative_humidity_pct": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "rain_mm": current.get("rain"),
            "showers_mm": current.get("showers"),
            "weather_code": current.get("weather_code"),
            "cloud_cover_pct": current.get("cloud_cover"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "wind_direction_deg": current.get("wind_direction_10m"),
            "wind_gusts_kmh": current.get("wind_gusts_10m"),
            "units": payload.get("current_units", {}),
            "attribution": "Open-Meteo — forecast/model-derived current conditions; not an on-road sensor reading.",
        }

    def get_current(self, *, allow_simulated_fallback: bool = False, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if self._cached and not force_refresh and now - self._cached_at <= self.cache_ttl_seconds:
            result = dict(self._cached)
            result["cache_status"] = "fresh_cache"
            return result
        try:
            data = self._fetch()
            self._cached = dict(data)
            self._cached_at = now
            data["cache_status"] = "network_refresh"
            return data
        except Exception as exc:
            if self._cached:
                age = max(0, int(now - self._cached_at))
                stale = dict(self._cached)
                stale.update({"status": "stale_cache", "cache_status": "stale_cache", "cache_age_seconds": age,
                              "last_error": f"{type(exc).__name__}: {exc}"})
                return stale
            if allow_simulated_fallback:
                return self.simulated_fallback()
            return {
                "status": "unavailable", "source": "none", "is_live": False, "is_simulated": False,
                "error": f"{type(exc).__name__}: {exc}",
                "message": "Open-Meteo is unavailable and simulated fallback was not enabled.",
            }

    @staticmethod
    def simulated_fallback(seed: int | None = None) -> dict[str, Any]:
        rng = random.Random(seed if seed is not None else 20261105)
        return {
            "status": "simulated_fallback", "source": "synthetic_weather_fallback",
            "is_live": False, "is_simulated": True,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "observation_time": None, "timezone": BHUBANESWAR["timezone"],
            "latitude": BHUBANESWAR["latitude"], "longitude": BHUBANESWAR["longitude"],
            "temperature_c": round(25 + rng.uniform(-3, 5), 1),
            "relative_humidity_pct": round(rng.uniform(50, 85), 1),
            "precipitation_mm": round(max(0, rng.gauss(0.8, 1.2)), 1),
            "rain_mm": round(max(0, rng.gauss(0.6, 1.0)), 1),
            "showers_mm": 0.0, "weather_code": None,
            "cloud_cover_pct": round(rng.uniform(15, 90), 1),
            "wind_speed_kmh": round(rng.uniform(3, 22), 1),
            "wind_direction_deg": None, "wind_gusts_kmh": None, "units": {},
            "cache_status": "not_cached",
            "notice": "SIMULATED WEATHER: this is generated fallback data, not a live API result.",
        }
