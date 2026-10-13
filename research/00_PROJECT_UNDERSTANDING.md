# 00 — What RESQ Actually Is Today

> Historical inventory of the initial fork. Priority 1 now implements full-graph
> A* in Brain, closures, hysteresis and measured routing evaluation. Read
> `docs/ROUTING_REVIEW_GUIDE.md` and `docs/routing-evaluation/routing-review.html`
> for the current implementation. The inventory below describes the earlier baseline.

This file is a factual inventory of the repository as it exists right now, built by
reading the source code directly (not the marketing language in the README). Every
claim below is tagged **IMPLEMENTED**, **PLANNED**, **ASSUMED/STAGED**, or **FUTURE**,
and points at the file that backs the claim.

RESQ is a **research prototype** for ambulance routing and traffic-signal
preemption. Its own docs are explicit about this: `README.md` line 3-7 states the
review build is "a deterministic demonstration, not a validated traffic simulation
or a trained-model result." Believe the code over the pitch deck.

---

## 1. What RESQ currently does (one paragraph)

RESQ runs a small FastAPI backend (`api/main.py`) that serves a **deterministic,
scripted demo** of an ambulance driving from a fixed origin to a fixed hospital in a
real (but tiny) slice of Chennai's OpenStreetMap road network
(`sim/chennai/roads.json`). Two fixed candidate routes exist: a "normal" route and a
"bypass" route, both computed once with real Dijkstra shortest-path search over the
OSM graph (`sim/chennai/roads.py`, class `RoadNetwork.route`). A toggle-able "hidden
incident" scenario fakes a blockage on the normal route; when a fake onboard camera
"sees" it, a fusion function (`fusion/engine.py`) marks that road near-closed, and a
hysteresis check (`routing/engine.py::should_switch_route`) decides whether to prefer
the bypass. A separate deterministic finite-state machine
(`corridor/state_machine.py`) models how a traffic signal would safely hand a green
phase to the ambulance. A React/TypeScript frontend (`web/`) visualizes all of this
live over a WebSocket, plus a "Replay" ghost-race view and a "Results" page showing
synthetic B0–B5 experiment statistics.

## 2. What is actually implemented (with file references)

| Capability | File | Notes |
|---|---|---|
| Real OSM road graph, one-way aware | `sim/chennai/roads.py`, `scripts/build_road_graph.py` | Downloaded once from the OSM API, checked into `sim/chennai/roads.json`. Real lat/lon geometry, real one-way tags. |
| Dijkstra shortest path over that graph | `sim/chennai/roads.py::RoadNetwork.route` | Classic Dijkstra with a min-heap (`heapq`). Assumed constant driving speeds (8.5 m/s roads, 5.5 m/s service roads), not real speed limits. |
| A second, reusable, general-purpose routing core | `routing/engine.py::RouteGraph.shortest_path` | Also Dijkstra, but the edge cost function `EdgeCost` can depend on **when** the edge is entered (`departure_time_s + elapsed`), so it is technically **time-dependent Dijkstra**, not plain distance Dijkstra. This is the piece most relevant to "dynamic routing." |
| Route-switch hysteresis | `routing/engine.py::should_switch_route` | Requires a candidate route to save ≥10s **and** ≥5% before switching, to stop route flip-flopping. |
| Confidence-aware sensor fusion | `fusion/engine.py::fuse_observations` | Inverse-variance weighting of a "macro" (city-wide) observation and a "perception" (onboard camera) observation of the same road's travel time. Includes a hard override: if perception reports a blockage with confidence ≥0.8, the road is forced to a 3600s ("near-closed") cost regardless of the macro estimate. |
| Forecasting baselines | `forecast/models.py` | Two explainable, non-ML baselines: `HistoricalAverageForecaster` (bucketed historical mean) and `SpatialTemporalForecaster` (persistence + neighbour-averaging heuristic). No trained model, no GNN. |
| Perception pipeline (data structures + rules) | `perception/pipeline.py` | `TrackedObject`, `BlockageDetector` (rule: ≥3 stopped objects across ≥2 lanes ⇒ blockage), `predict_tracks` (constant-velocity 2s projection), `cluster_lidar` (deterministic Euclidean/DBSCAN-like clustering). No neural network, no YOLO. |
| Signal preemption state machine | `corridor/state_machine.py::IntersectionController` | Deterministic FSM: NORMAL → REQUEST_VALID → PRE_CLEAR → SAFE_TRANSITION → EV_SERVICE → EV_PASSED → RECOVERY → NORMAL. Enforces minimum green, amber, all-red, max preemption, recovery timers. ML/prediction can *request* priority but cannot skip these safety states. |
| Crowd-GPS probe simulation | `sim/probes/generator.py` | `ProbeGenerator` (adoption %, GPS noise, dropout, upload lag) and `ProbeAggregator` (median/MAD robust aggregation per road). |
| Seeded B0–B5 experiment runner | `experiments/runner.py` | A hand-tuned formula-based "mesoscopic" model (not a real traffic simulator) that produces synthetic travel-time metrics per baseline/scenario/seed. Explicitly documented as not a substitute for SUMO. |
| SQLite run store + stats | `recorder/store.py` | Persists `RunMetrics`, computes mean/95% CI/p95 per baseline. |
| Ghost replay generator | `recorder/replay.py` | Produces a synchronized "regular vs ResQ" replay using the same real road geometry and the staged 120s/8s assumptions. |
| REST + WebSocket API | `api/main.py` | `/api/snapshot`, `/api/controls`, `/api/experiments/run`, `/api/results`, `/api/replays/ghost`, `/ws/live` (5 Hz). |
| Shared strict schema | `schema/models.py` | Pydantic v2 models (`extra="forbid"`) shared by backend and (via generated types) the frontend. This is the contract every module communicates through. |
| React frontend | `web/src/*.tsx` | Live map, Replay, Cab (driver view), Results dashboard. |
| Optional adapter interfaces | `adapters/sumo_traci.py`, `adapters/carla_sensors.py`, `adapters/google_routes.py` | Real integration code exists and is unit-testable, but each one raises `AdapterUnavailable` unless its external dependency (SUMO+TraCI install, CARLA server, Google API key) is actually present. `/api/adapters` reports live availability. |

## 3. What is only planned (explicitly, in the docs, not in code)

Per `README.md` and `docs/REVIEW_II_VIVA_NOTES.md`, these are **future work**, not
present in any Python module today:

- YOLO / trained object detection for perception (`perception/pipeline.py` consumes
  already-tracked objects; it does not run inference on images).
- A trained traffic forecasting model (GNN / graph neural network) to replace
  `forecast/models.py`'s two explainable baselines.
- A validated SUMO traffic simulation of the real intersection network (the SUMO
  *adapter* exists and is testable in isolation, but nothing in the live demo path
  calls it).
- CARLA-driven sensor realism connected to the live demo.
- Real signal hardware / municipal traffic-control integration.
- Training or evaluating on PEMS-BAY/METR-LA or real Chennai traffic history.

## 4. Current routing pipeline (as code, not aspiration)

```text
sim/chennai/roads.json (real OSM graph, one-way aware)
        │
        ▼
RoadNetwork.route() — Dijkstra shortest path, constant assumed speeds  [sim/chennai/roads.py]
        │
        ▼
demo_plan() picks ONE fixed "normal" path and ONE fixed "bypass" path,
by deliberately blocking a section of the normal route and re-running Dijkstra
        │
        ▼
DemoSimulation.snapshot() [sim/demo.py] decides, per tick, whether to report
route-a or route-b as "selected", using should_switch_route() hysteresis
        │
        ▼
NetworkSnapshot (schema/models.py) served via /api/snapshot and /ws/live
```

Separately, `routing/engine.py::RouteGraph` is a **general, reusable Dijkstra-based
time-dependent router** — it is unit tested (`tests/test_routing.py`) but is **not
currently wired into `sim/demo.py`**. The live demo's actual path selection is the
simpler, two-fixed-routes logic in `sim/demo.py`. This is an important distinction:
RESQ *has* time-dependent Dijkstra code, but the live demo does not yet call it for
turn-by-turn dynamic replanning across an arbitrary graph — it precomputes two paths
once at import time via `@lru_cache`.

## 5. Current traffic-signal pipeline

```text
PriorityRequest (from routing/ambulance ETA) ──▶ IntersectionController.update()
                                                          │
                    deterministic FSM, timers only:
       NORMAL → REQUEST_VALID → PRE_CLEAR → SAFE_TRANSITION
             → EV_SERVICE → EV_PASSED → RECOVERY → NORMAL
                                                          │
                                                          ▼
                                                   SignalState (conflicting_green always False)
```

No ML model is allowed to shortcut this. This is a deliberate safety design choice
documented in `docs/REVIEW_II_VIVA_NOTES.md` §5.

## 6. Current data/fusion pipeline

```text
macro observation (city-wide feed)  ──┐
                                        ├──▶ fuse_observations() ──▶ RoadEstimate
perception observation (onboard cam)──┘        (inverse-variance weighting,
                                                  blockage override if conf ≥ 0.8)
```

`fuse_observations` (fusion/engine.py) is the closest thing in this repo to a
"decision fusion layer." It fuses exactly two observation sources per road segment
and returns one `RoadEstimate.travel_time_s` + `confidence` + `blockage` flag. It
does **not** currently touch routing directly — `sim/demo.py` calls it to build
`roads: list[RoadEstimate]` for display, but the actual route choice in the demo
uses hardcoded ETAs (`INCIDENT_QUEUE_S`, `RESQ_CUE_S`), not `RoadEstimate.travel_time_s`
as live edge costs. (`routing/engine.py::RouteGraph.shortest_path` *is* designed to
consume a time-dependent edge-cost function, which is exactly where fused estimates
would plug in — see file `31_FINAL_ROUTING_ARCHITECTURE.md`.)

## 7. Where ML prediction would enter

Today: nowhere at runtime. `forecast/models.py` provides two **non-ML, explainable**
baselines (historical bucket average; persistence + spatial smoothing). They are
deliberately simple placeholders documented as "before GPU-based graph models are
introduced" (module docstring, `forecast/models.py` line 1). No file in the repo
currently calls a trained model at inference time. The natural integration point is
as a *drop-in replacement* for `SpatialTemporalForecaster.predict`, producing the
same `SpeedForecast` output shape, feeding into fusion the same way a macro
observation does.

## 8. Where YOLO/perception would enter

Today: `perception/pipeline.py` expects a `list[TrackedObject]` already resolved
into class, lane, distance, speed, and confidence — i.e., it consumes YOLO+tracker
*output*, not raw pixels. There is no image-in code path anywhere in the repo. The
integration point is upstream of `BlockageDetector.observe`: a real detector +
tracker (e.g., YOLO + ByteTrack, mentioned as future work) would need to produce
`TrackedObject` instances from `adapters/carla_sensors.py`'s camera/LiDAR queues (or
real hardware) each frame.

## 9. Where traffic data would enter

Today: `sim/probes/generator.py` simulates crowd-GPS probes from *synthetic*
`VehicleTruth` objects — there is no real GPS ingestion path. `adapters/google_routes.py`
is a real, working HTTP client for Google Routes v2 traffic data, gated behind
`GOOGLE_ROUTES_API_KEY`; it is implemented but not wired into the live snapshot loop.
`adapters/sumo_traci.py` would supply live vehicle/signal state from a running SUMO
simulation, also not wired into the live demo.

## 10. What the final routing decision is expected to consume

Based on the schema (`schema/models.py`) and docstrings, the intended (not fully
wired) inputs to a road-segment cost are:
- A macro travel-time observation with age and confidence (`RoadObservation`,
  `source=macro`).
- A perception travel-time observation with distance and confidence
  (`RoadObservation`, `source=perception`).
- The fused `RoadEstimate` (travel_time_s, confidence, blockage).
- Possibly a forecast (`SpeedForecast`) for a future arrival time at that edge.

## 11. What outputs the routing system produces

- `PathResult` (`routing/engine.py`): an ordered tuple of edge IDs + total travel
  time.
- `RoadPath` (`sim/chennai/roads.py`): node/edge/name sequence + distance + travel
  time + lat/lon geometry.
- `RouteOption` (`schema/models.py`): the API/UI-facing shape — route id, label,
  edge ids, ETA, whether it's currently selected, a human-readable reason string,
  and geometry for map rendering.
- `PriorityRequest` / `SignalState`: signal-side outputs consumed by the corridor
  controller and displayed in the UI.

## 12. Current limitations and assumptions (stated plainly)

- **Only two candidate routes exist** in the live demo (`route-a`, `route-b`),
  precomputed once — this is not general k-route or arbitrary-origin/destination
  routing at runtime.
- **Driving speeds are assumed constants** (8.5 m/s / 5.5 m/s), not derived from OSM
  `maxspeed` tags or real telemetry.
- **The incident queue (120s) and ResQ detection cue (8s) are hardcoded constants**
  (`sim/demo.py` lines 28-29), explicitly labeled "staged inputs, not empirical
  performance measurements."
- **No turn restrictions, lane rules, or general closures** are modeled — only the
  one deliberately staged incident section (`README.md` line 98-100).
- **The B0–B5 experiment numbers are a formula, not a simulation** — `experiments/runner.py`
  computes synthetic metrics from hand-tuned distributions, not from actually
  running vehicles through a network.
- **Fusion currently only combines exactly two sources** (macro, perception) for a
  single road, not an arbitrary sensor set.
- **The general-purpose time-dependent `RouteGraph` (routing/engine.py) is not yet
  connected to the live OSM-backed demo** — they are two separate routing code
  paths today.

---

## What you should understand before reading the next file

You should be able to answer, in your own words: *What are the two different
"routers" in this codebase, and why does one use time-dependent edge costs while the
other doesn't (yet)?* And: *Which three modules would a real ML/YOLO integration
touch, and what shape of data would each one need to produce?* If you can answer
those, move on to `01_ROUTING_PROBLEM.md`.
