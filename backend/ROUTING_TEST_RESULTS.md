# Routing and incident execution results

> **Historical v2 report.** Written before the v3 final release. Statements here about TomTom mapping, test counts, dashboard launch, screenshots, or dependency/runtime versions are superseded by [`FEATURE_STATUS.md`](FEATURE_STATUS.md).

## Graph source used

The runtime did not have `osmnx` installed. `OSMNetworkProvider` therefore logged `ModuleNotFoundError: No module named 'osmnx'` and loaded its explicitly labelled `synthetic_fallback` graph.

- Nodes: 8
- Directed edges: 24
- Weakly connected: Yes
- Coordinates and road names: illustrative placeholders, not actual Bhubaneswar road geometry

## Dijkstra and A* same-pair comparison

Both algorithms were run with the same fallback graph and edge costs.

| Origin → destination | Algorithm | Node sequence | Distance | Estimated ETA |
|---|---|---|---:|---:|
| 1 → 8 | Dijkstra | 1 → 3 → 8 | 1.65 km | 2.83 min |
| 1 → 8 | A* | 1 → 3 → 8 | 1.65 km | 2.83 min |
| 2 → 7 | Dijkstra | 2 → 4 → 7 | 2.58 km | 4.42 min |
| 2 → 7 | A* | 2 → 4 → 7 | 2.58 km | 4.42 min |
| 4 → 8 | Dijkstra | 4 → 1 → 3 → 8 | 2.75 km | 4.71 min |
| 4 → 8 | A* | 4 → 1 → 3 → 8 | 2.75 km | 4.71 min |

The best-route cost matched for all three pairs. Route geometry and estimated ETAs above are fallback-graph results, not real-road measurements or live traffic ETAs.

## Incident and road-closure experiment

For origin 1 → destination 8, the base shortest path was `1→3→8` (1.65 km; 2.83 min).

| Scenario | Selected path | Distance | ETA | Route changed? |
|---|---|---:|---:|---|
| No incident | 1 → 3 → 8 | 1.65 km | 2.83 min | Baseline |
| Severity-1 accident on 1 → 3 | 1 → 5 → 8 | 2.30 km | 3.94 min | Yes |
| Road closure on 1 → 3 | 1 → 5 → 8 | 2.30 km | 3.94 min | Yes |

The closure test also verified that directed edge 1→3 is not present in the selected post-closure route. Incident expiry and explicit removal are covered by automated tests.

## Cost model described in the dashboards

The new `routing/route_optimizer.py` applies the following simulated routing multipliers:

- Congestion: Low 1.00×, Medium 1.25×, High 1.75×.
- Rainfall: multiplier `1 + 0.012 × min(rainfall_mm, 30)` (up to 1.36×).
- Accident: multiplicative factor `1 + 2 × severity`, plus 30–210 seconds of additive delay.
- Roadwork / obstruction / hazard: multiplicative factor `1 + 0.8 × severity`, plus 15–105 seconds of additive delay.
- Road closure: removes the directed edge from the routing graph.

The retained legacy simulation engine uses a distinct model: Low 1×, Medium 4×, High 12×, and accident multiplier 500×, with its own per-segment ETA cap. The legacy dashboard's labels have been corrected to these exact values. Do not compare the two dashboards' penalty values as if they were one cost function.

Console evidence: `verification/routing_incident_execution.log`. Automated routing tests: `verification/pytest_after_fixes.log`.
