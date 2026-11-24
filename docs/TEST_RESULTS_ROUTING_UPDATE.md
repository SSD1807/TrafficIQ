# Test results - incident-aware routing update

Everything below was run in an offline sandbox (no internet, no TomTom key). That sandbox had **no** fastapi, pydantic,
osmnx, shapely, streamlit, xgboost, pytest or npm packages, so a **sandbox-only** stand-in for pytest/httpx was used to
execute plain-Python tests. Those stand-ins are NOT shipped. Results on your machine come from the real pytest.

## Executed here and passing (123 tests)
| Module | Tests | What it proves (real cached OSM graph unless noted) |
|---|---|---|
| `test_geocoding.py` | 8 | Nominatim request restricted to India + graph bbox, user agent sent, spelling variant fallback, local road names without network, outside-network and low-similarity hits rejected, outage reported as `geocoder_unavailable` (not "not found") - **fake Nominatim** |
| `test_incident_engine.py` | 26 | Grid + real OSM: no incident, confirmed closure -> alternative that avoids the closed edge, major accident priced not blocked (reroute when delay large), minor accident keeps route with warning, unrelated incident leaves route unchanged, unmatched incident shown but not applied, no-alternative -> `no_route`, rules configurable; **Dijkstra and A\*** both; TomTom payloads **mocked** with geometry taken from real OSM edges |
| `test_route_planning.py` | 11 | geocode -> snap -> plan on the real graph with fake Nominatim/TomTom; incident refresh failure does not block routing; live data unavailable -> never "incident-free"; geocoder outage 503; unknown place 404; point outside network / unknown node / same node rejected; real road geometry endpoint is de-duplicated and capped |
| `test_incident_matching.py` (+2 new tests) | counted in the 78 below | name-hint tie-break and ambiguity listing keep the "ambiguous stays unmatched" rule |
| Pre-existing modules runnable offline (incl. matching, manager, impact, routing) | 78 | no regression |

**Regression check:** the original project's tests were run in the same sandbox; the set of failing tests after my changes is
identical to the original's (all remaining failures are missing packages or my httpx stand-in), apart from 4 real-OSM tests that
now run instead of failing on a missing osmnx.

## NOT executed here (need packages or internet) - run on Windows
```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q                       # full suite incl. tests\test_plan_api.py (HTTP endpoints, new)
..\.venv\Scripts\python.exe -m pytest -q tests\test_incident_engine.py tests\test_route_planning.py tests\test_geocoding.py
cd ..\frontend; npm install; npm run typecheck; npm run lint; npm test; npm run build
```
Not run at all: backend startup, frontend startup/build/typecheck/vitest, the new `/routes/plan`, `/locations/search`,
`/network/*` endpoints over HTTP, the existing and new React tests, forecasting/ML/weather/Copilot tests that need
xgboost/httpx, any live Nominatim or TomTom call, and a visual check of the pages. The TypeScript was only syntax-checked
(`tsc` reported no syntax or undefined-name errors; type errors that depend on react/lucide types could not be evaluated).

Live checks to do once, with internet and a TomTom key:
```powershell
.\start_backend.bat
curl http://127.0.0.1:8000/network/status
curl "http://127.0.0.1:8000/locations/search?q=Jharapada,%20Bhubaneswar"
curl "http://127.0.0.1:8000/locations/search?q=Baragada,%20Bhubaneswar"
```
Whether Nominatim finds Jharapada/Baragada (or only a spelling variant such as Badagada) could not be confirmed offline; the
response lists every attempt (`geocode.attempts`) so a miss is diagnosable.
