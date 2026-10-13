# 31 — Final Recommended Architecture

## The diagram

```text
                    ┌────────────────────────┐
                    │   Camera / LiDAR feed    │   (real hardware or adapters/carla_sensors.py)
                    └────────────┬─────────────┘
                                 │ raw frames / point clouds
                                 ▼
                    ┌────────────────────────┐
                    │   PERCEPTION            │   perception/pipeline.py
                    │   (tracking, blockage,  │   FUTURE: real YOLO + tracker upstream of
                    │   short-horizon predict)│   TrackedObject construction
                    └────────────┬─────────────┘
                                 │ RoadObservation(source=perception)
                                 │
   ┌─────────────────────┐      │
   │ Macro traffic feed /│      │
   │ crowd-GPS probes     │      │
   └──────────┬───────────┘      │
              │ VehicleTruth/probe samples
              ▼                  │
   ┌─────────────────────┐      │
   │ PREDICTION           │      │
   │ forecast/models.py    │      │
   │ (historical baseline, │      │
   │  spatial-temporal;    │      │
   │  FUTURE: trained GNN) │      │
   └──────────┬───────────┘      │
              │ SpeedForecast /   │
              │ RoadObservation(source=macro)
              ▼                  ▼
        ┌────────────────────────────┐
        │  FUSION                     │   fusion/engine.py::fuse_observations
        │  (inverse-variance combine, │   EXTEND: accept forecast horizon input,
        │   blockage override)        │   not just current-instant observations
        └──────────────┬───────────────┘
                        │ RoadEstimate (travel_time_s, confidence, blockage)
                        ▼
        ┌────────────────────────────┐
        │  DYNAMIC EDGE-COST CALC     │   NEW (proposed): scalarized multi-objective
        │  (weighted-sum: time, risk, │   cost function — see 10_GOAT_DECISION_ENGINE.md
        │   confidence, stability)    │   and algorithms/MULTI_OBJECTIVE_ROUTING.md
        └──────────────┬───────────────┘
                        │ EdgeCost(edge, time) -> float
                        ▼
        ┌────────────────────────────┐
        │  ROUTING ALGORITHM          │   routing/engine.py::RouteGraph.shortest_path
        │  Time-dependent Dijkstra,   │   (already time-dependent-capable)
        │  + A* heuristic (proposed), │
        │  + D* Lite replanning       │
        │  (proposed, for efficient   │
        │  incident reaction)         │
        └──────────────┬───────────────┘
                        │ RouteOption (edges, ETA per intersection, geometry)
                        ▼
        ┌────────────────────────────┐
        │  SIGNAL PREEMPTION          │   corridor/state_machine.py
        │  Deterministic FSM:         │   (unchanged — already correct)
        │  NORMAL→REQUEST_VALID→      │
        │  PRE_CLEAR→SAFE_TRANSITION  │
        │  →EV_SERVICE→EV_PASSED→     │
        │  RECOVERY→NORMAL            │
        └──────────────┬───────────────┘
                        │ SignalState
                        ▼
              ┌───────────────────┐
              │  Ambulance driver /│   web/src (Live, Cab, Replay, Results)
              │  control room UI    │   api/main.py (REST + WebSocket)
              └───────────────────┘
```

## Why this exact shape, derived (not assumed)

The brief's own example diagram (YOLO → Perception → [Traffic data, ML prediction]
→ Fusion → Cost Update → GOAT Layer → Route Engine → Signal Preemption → Ambulance)
is close, but this document derives a corrected version by checking it against
RESQ's actual module boundaries and data contracts rather than accepting it
as-is. Two corrections worth calling out explicitly:

1. **"GOAT Layer" is renamed/split into two concrete stages**: FUSION (combining
   redundant estimates of the *same* quantity — macro vs. perception travel time,
   which is what `fuse_observations` genuinely does today) and a separate
   **DYNAMIC EDGE-COST CALCULATION** stage (combining *different kinds* of concern
   — time, risk, confidence, stability — into the one number the pathfinder needs).
   Merging these two into one "GOAT layer" would blur a statistics problem (fusion)
   with a value-judgment/weighting problem (cost calculation) — see
   `10_GOAT_DECISION_ENGINE.md` §1-3 for the full argument.
2. **Prediction feeds fusion, not the other way around** — a forecast is itself an
   *input* to producing a trustworthy travel-time estimate for a future arrival
   time; it is not a separate parallel path that skips fusion.

## Every arrow explained

- **Camera/LiDAR → Perception**: raw sensor data becomes structured, confidence-scored
  `TrackedObject`s / `RoadObservation`s. Today this is a rules-based pipeline
  (`BlockageDetector`); the arrow's *shape* (raw frames in, structured observations
  out) is exactly where a real YOLO+tracker would plug in without changing anything
  downstream.
- **Macro feed/probes → Prediction**: historical and recent speed samples feed a
  forecaster that predicts future speed per road per horizon. Today: explainable
  baselines (`forecast/models.py`); future: a trained model with the same output
  shape.
- **Perception → Fusion** and **Prediction → Fusion**: both produce
  `RoadObservation`-shaped (or forecast-shaped) estimates of the same underlying
  quantity (a road's travel time), which fusion combines with confidence-aware
  weighting — this arrow already exists and works (`fuse_observations`).
- **Fusion → Dynamic Edge-Cost Calculation**: the fused, confident travel-time
  estimate becomes one *input* among several (alongside risk/stability) to the
  final scalar cost — this stage does not exist yet as a distinct module; see the
  implementation plan.
- **Edge-Cost Calculation → Routing Algorithm**: the routing algorithm receives an
  `EdgeCost`-shaped function and doesn't need to know what went into it — this
  interface already exists (`routing/engine.py`'s `EdgeCost` type).
- **Routing Algorithm → Signal Preemption**: the chosen route's ETA to each
  upcoming intersection becomes a `PriorityRequest`; the corridor controller reacts
  to this but never influences the route choice itself — preserving the safety
  separation already present in `corridor/state_machine.py`.
- **Signal Preemption → Ambulance/UI**: `SignalState` and `RouteOption` are both
  served to the frontend via the existing API/WebSocket layer, unchanged by
  anything proposed here.

## What this diagram deliberately does NOT change

- `corridor/state_machine.py` — already correctly isolated and safety-first; no
  proposed change touches its internal logic.
- `schema/models.py`'s existing contracts — the proposal reuses `RoadObservation`,
  `RoadEstimate`, `RouteOption`, `PriorityRequest`, `SignalState` as-is; only a new
  cost-calculation step and (optionally) new fields for cost breakdown are added.
- The API/WebSocket layer's external shape.

## What you should understand before reading the next file

You should be able to redraw this diagram from memory (boxes and arrows are
enough, exact wording isn't required) and explain, for any single arrow, what data
crosses it and which existing RESQ file already implements that side of the
boundary. Next: `40_ROUTING_IMPLEMENTATION_PLAN.md`, which turns this diagram into
concrete file-level changes (still without writing any code).
