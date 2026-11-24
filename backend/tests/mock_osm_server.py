"""Local stand-in for Nominatim + Overpass so OSMnx's real download code path can run offline.

Serves the TEST-ONLY grid in fixtures/mini_grid.osm. It validates our provider's download/cache/fallback
logic and OSMnx integration; it says nothing about the availability of the public OSM servers.
"""
from __future__ import annotations

import json
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "mini_grid.osm"


def _overpass_json() -> dict:
    root = ET.parse(FIXTURE).getroot(); elements = []
    for n in root.iter("node"):
        elements.append({"type": "node", "id": int(n.get("id")), "lat": float(n.get("lat")), "lon": float(n.get("lon"))})
    for w in root.iter("way"):
        elements.append({"type": "way", "id": int(w.get("id")), "nodes": [int(nd.get("ref")) for nd in w.iter("nd")],
                         "tags": {t.get("k"): t.get("v") for t in w.iter("tag")}})
    return {"version": 0.6, "generator": "mock-overpass", "elements": elements}


class MockOSM(ThreadingHTTPServer):
    def __init__(self, *, geocode_ok: bool = True, overpass_ok: bool = True):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.geocode_ok, self.overpass_ok, self.requests = geocode_ok, overpass_ok, []
        self.payload = _overpass_json()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def __enter__(self):
        self.thread.start(); return self

    def __exit__(self, *exc):
        self.shutdown(); self.server_close()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep test output clean
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _handle(self):
        path = self.path.split("?")[0]; self.server.requests.append(path)
        if path.endswith("/status"):
            return self._send(200, b"Connected as: 127.0.0.1\nCurrent time: 2026-11-06T00:00:00Z\nAnnounced endpoint: none\nRate limit: 2\n2 slots available now.\n", "text/plain")
        if path.endswith("/search"):
            if not self.server.geocode_ok:
                return self._send(200, b"[]")
            ring = [[85.80, 20.28], [85.85, 20.28], [85.85, 20.33], [85.80, 20.33], [85.80, 20.28]]
            return self._send(200, json.dumps([{"place_id": 1, "osm_type": "relation", "osm_id": 1, "lat": "20.3", "lon": "85.82",
                "display_name": "Mock City", "class": "boundary", "type": "administrative", "importance": 0.5, "place_rank": 16,
                "boundingbox": ["20.28", "20.33", "85.80", "85.85"], "geojson": {"type": "Polygon", "coordinates": [ring]}}]).encode())
        if "interpreter" in path:
            if not self.server.overpass_ok:
                return self._send(503, b"overloaded", "text/plain")
            n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
            return self._send(200, json.dumps(self.server.payload).encode())
        return self._send(404, b"{}")

    do_GET = do_POST = _handle
