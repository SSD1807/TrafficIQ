# HERE traffic provider - setup and behaviour

The backend selects its live-traffic provider from `backend\.env`:

```
TRAFFIC_PROVIDER=here        # here (default) | tomtom | none
HERE_API_KEY=                # paste your key here (backend\.env ONLY)
```

## Get a HERE API key
1. Create a free account at <https://platform.here.com> (the Limited/Freemium plan; check the current pricing page for limits).
2. Open **Access Manager -> Apps -> Register new app**, then on the app's **Credentials** tab choose **API Keys -> Create API key**.
3. Make sure the project/app can use **Traffic API v7**, **Routing API v8** and **Traffic Raster Tile API v3** (service linking is done in the platform portal).
4. Put the key in `backend\.env` as `HERE_API_KEY=...`. Never put it in `frontend\.env`, a `VITE_*` variable, or source code.

## What the app does with HERE
| Capability | HERE service | Where |
|---|---|---|
| Colour-coded flow segments + data-state label | Traffic API v7 `/flow` (bbox, shape) | `GET /traffic/flow` -> Traffic Map page |
| Full-network tile overlay | Traffic Raster Tile API v3 `/flow` | `GET /traffic/tiles/{z}/{x}/{y}.png` (server-side proxy, key stays in the backend) |
| Traffic-aware OSM routing | flow speeds matched to **directed** OSM edges, then Dijkstra/A* re-run | `POST /routes/calculate` with `"use_live_traffic": true` |
| Reference ETA (traffic vs free-flow) | Routing API v8 `duration` vs `baseDuration` | `provider_eta` in the route response |

Routing is called traffic-aware **only** when `traffic_flow.status == "applied"` (measurements matched to at least one directed OSM edge). Otherwise the OSM result is returned unchanged and `traffic_flow.status` says why.

## Data-state labels (never mixed)
`live` fetched now | `cached` fetched earlier, inside the fresh TTL (age shown, not reported live) | `stale_cache` provider failed, older data shown, explicitly **not live** | `unavailable` | `disabled` (no key / `TRAFFIC_PROVIDER=none`).
Simulated congestion is a separate option (`use_simulated_congestion`) and keeps its own SIMULATED labels; it is never substituted for provider data.

## Coverage claim
Nothing in this project asserts that HERE has live coverage in Bhubaneswar. `GET /traffic/provider/status` reports `coverage_observed: true` only after a real HERE flow request for the Bhubaneswar window returned segments in the running backend. Run `python scripts\live_test_here.py` to test it.

## Unverified assumptions (confirm with the live test)
* Flow speeds are metres per second (converted x3.6) - the live test prints median speeds so you can sanity-check.
* Flow shape points are ordered in the direction of travel (used for directed-edge matching).
* The traffic tile endpoint accepts the `apiKey` query parameter (HERE also documents Bearer-token auth).
* Free-tier numbers differ between sources; check HERE's pricing page. Caching (flow 120 s, ETA 60 s, tiles 300 s), request timeouts (8 s) and a pause after HTTP 429 limit usage.

## Switching providers
`TRAFFIC_PROVIDER=tomtom` (needs `TOMTOM_API_KEY`) uses the existing TomTom implementation unchanged; `none` disables external traffic. Restart the backend after editing `.env`.
