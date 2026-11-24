"""Optional TomTom Traffic API adapter for road flow and incident summaries."""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

BASE_URL = "https://api.tomtom.com"


class TomTomService:
    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None, timeout_seconds: float = 8.0):
        self.api_key = api_key or os.getenv("TOMTOM_API_KEY")
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, tuple[float, Any]] = {}

    def _get_cached(self, key: str, ttl: int, url: str, params: dict) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "disabled", "source": "tomtom", "is_live": False,
                    "message": "TOMTOM_API_KEY is not configured."}
        now = time.monotonic()
        if key in self._cache and now - self._cache[key][0] <= ttl:
            data = dict(self._cache[key][1]); data["cache_status"] = "fresh_cache"; return data
        try:
            response = self.client.get(url, params={**params, "key": self.api_key}, timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
            result = {"status": "ok", "source": "tomtom_traffic_api", "is_live": True,
                      "fetched_at": time.time(), "data": data, "cache_status": "network_refresh"}
            self._cache[key] = (now, result)
            return result
        except Exception as exc:
            # httpx error text embeds the request URL, which contains ?key=...; never return or log the key.
            text = str(exc).replace(self.api_key, "***") if self.api_key else str(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                text = f"HTTP {exc.response.status_code} from TomTom"
            return {"status": "error", "source": "tomtom_traffic_api", "is_live": False,
                    "error": f"{type(exc).__name__}: {text}"}

    def flow_at(self, latitude: float, longitude: float) -> dict[str, Any]:
        url = f"{BASE_URL}/traffic/services/4/flowSegmentData/absolute/10/json"
        return self._get_cached(
            f"flow:{latitude:.4f}:{longitude:.4f}", 180, url,
            {"point": f"{latitude},{longitude}", "unit": "kmph"},
        )

    def incidents(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> dict[str, Any]:
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ValueError("Invalid bounding box")
        url = f"{BASE_URL}/traffic/services/5/incidentDetails"
        params = {
            "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "fields": "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,events{description,code,iconCategory},startTime,endTime,from,to,length,delay,roadNumbers,timeValidity,probabilityOfOccurrence}}}",
            "language": "en-GB", "timeValidityFilter": "present",
        }
        return self._get_cached(f"incidents:{min_lon}:{min_lat}:{max_lon}:{max_lat}", 120, url, params)
