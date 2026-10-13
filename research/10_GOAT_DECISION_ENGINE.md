# 10 — The "GOAT" Decision Layer: What It Should Be

## A note on terminology, stated honestly

The term "GOAT decision engine" does **not** appear anywhere in this repository —
I grepped the entire codebase for "GOAT"/"goat" and found zero matches. This is not
an implemented, named component. I'm treating it, as the brief instructs, as the
*concept* being described: a layer that combines multiple information sources (ML
prediction, perception, traffic data, incident info, confidence, signal state) into
a final routing decision. The closest existing thing in RESQ to this concept is
`fusion/engine.py::fuse_observations`, but that function is narrower than the full
"GOAT" concept the brief describes — it only fuses two *travel-time* observations
for *one* road, not the full multi-source decision described in the brief. This
file proposes what the fuller version should look like, clearly labeled as
proposed/future, not existing.

## 1. Should everything be combined into ONE algorithm, or kept separate?

**Kept separate — this is the single most important architectural decision in this
whole research set.** Here's why, concretely:

Consider what each input actually *is*:
- YOLO/perception output: raw, noisy, needs tracking, filtering, confidence scoring
  — this is a **signal processing** problem.
- ML traffic prediction: a **forecasting/regression** problem — predicting a future
  number (speed/congestion) from historical and spatial patterns.
- Fusion: a **statistics** problem — combining multiple noisy estimates of the same
  quantity into one trustworthy estimate with a confidence value.
- Pathfinding: a **graph search** problem — given costs, find the cheapest path.
- Signal preemption: a **safety-critical control** problem — must never violate
  hard physical/safety invariants (no conflicting greens, minimum timings).

These are five *different kinds of computational problem*, each with different
correctness criteria, different failure modes, and (crucially) different testing
strategies. RESQ's own codebase already reflects this separation — it did not
happen by accident. Look at the module boundaries: `perception/`, `forecast/`,
`fusion/`, `routing/`, `corridor/` are five separate top-level packages, each
independently unit-tested (`tests/test_perception.py`, `tests/test_fusion.py`,
`tests/test_routing.py`, `tests/test_corridor.py`, etc.). `schema/models.py`
defines the strict contracts (`RoadObservation`, `RoadEstimate`, `RouteOption`,
`PriorityRequest`, `SignalState`) that let these modules communicate *without*
knowing about each other's internals.

`docs/REVIEW_II_VIVA_NOTES.md` states this design rule explicitly (§4): *"the
important design rule is separation of concerns. Sensor-specific objects should not
leak into routing or the browser."*

If everything were combined into one monolithic algorithm/model:
- You could not test the pathfinder without a working perception pipeline.
- You could not swap a real trained forecasting model in for the current baseline
  without touching routing code (defeating the whole point of the baseline
  placeholder, per `forecast/models.py`'s own docstring: *"used before GPU graph
  models are introduced"*).
- You could not explain a routing decision without also explaining the perception
  model's internals — a serious problem for a safety review or viva.
- A bug or change in one area (e.g., retraining the forecaster) could silently
  change unrelated behavior (e.g., signal timing) in ways nobody could predict.

So the correct shape is a **pipeline of independently-testable stages**, not one
fused algorithm:

```text
PERCEPTION → PREDICTION → FUSION → ROUTE COST → ROUTING ALGORITHM → SIGNAL PREEMPTION
```

Each arrow is a **typed contract** (a `schema/models.py` class), not a shared
internal state. This is exactly RESQ's existing architectural instinct, just not
yet fully wired end-to-end for the cost side of routing.

## 2. What each stage's job actually is

- **PERCEPTION** (`perception/pipeline.py`): turn raw sensor input (camera/LiDAR,
  or in future, real YOLO+tracker output) into structured `TrackedObject`s and
  `RoadObservation`s. Job: "what do I see, right now, right here, and how sure am
  I?" Not concerned with routes, forecasts, or signals at all.
- **PREDICTION** (`forecast/models.py`, future trained models): turn historical +
  recent data into a `SpeedForecast` for a future time horizon. Job: "what will
  this road likely look like when the ambulance actually gets there?" Not
  concerned with what any *particular* vehicle should do.
- **FUSION** (`fusion/engine.py`, extended per this file): combine perception +
  prediction (+ macro data) for a given road into one trustworthy `RoadEstimate`
  with a confidence value. Job: "given everything we know, what's the best single
  estimate for this road segment's cost, and how sure are we?"
- **ROUTE COST** (proposed, not yet a named module — see `40_ROUTING_IMPLEMENTATION_PLAN.md`):
  turn a `RoadEstimate` (+ optionally risk/stability factors) into the single
  scalar number the pathfinder actually minimizes. Job: "given this estimate, and
  RESQ's priorities (safety vs. speed), what number should the pathfinder see for
  this edge, at this time?" This is the multi-objective scalarization described in
  `algorithms/MULTI_OBJECTIVE_ROUTING.md`.
- **ROUTING ALGORITHM** (`routing/engine.py`): given edge costs, find the cheapest
  path. Job: pure graph search — does not know or care *why* an edge costs what it
  costs.
- **SIGNAL PREEMPTION** (`corridor/state_machine.py`): given the chosen route and
  the ambulance's ETA to each intersection, safely coordinate signal timing. Job:
  enforce hard safety invariants; *react to* routing's output, never *drive*
  routing's decision.

## 3. Proposed cost-function design (explicitly proposed, not RESQ's current code)

Conceptually, extending what `fusion/engine.py` already does for one road:

```text
edge_cost(edge, time) =
      w_time        * fused_travel_time_s(edge, time)
    + w_risk         * blockage_risk(edge, time)
    + w_uncertainty  * (1 - fused_confidence(edge, time))
    + w_stability     * route_change_penalty(edge, current_route)
```

Do **not** blindly copy this formula or these weights — the brief explicitly warns
against this, and for good reason: the *right* weights depend on decisions RESQ's
team must make deliberately (how much extra time is worth avoiding how much
risk?), and should be documented and testable, not guessed. What matters is the
*shape*: one clearly-labeled term per concern, each traceable back to a specific
upstream module's output, so a reviewer can ask "why did edge X cost this much?"
and get a real answer term-by-term — this is precisely the practical value of
scalarization over a black-box combination (see `algorithms/MULTI_OBJECTIVE_ROUTING.md`
§16).

**Where the existing blockage override fits in**: `fusion/engine.py`'s current
hard override (force to 3600s if perception blockage confidence ≥ 0.8) is a
reasonable *extreme case* of the `w_risk * blockage_risk` term above — not a
different mechanism, just a steep one. This file's proposal generalizes it rather
than replacing it.

## 4. How the GOAT/fusion layer differs from the pathfinding algorithm — the key distinction

This is worth stating as bluntly as possible, because it's the most commonly
confused point for beginners:

- The **fusion/decision layer** answers: *"What does this road segment cost,
  right now/at this predicted time, given everything we know and how sure we
  are?"* — this involves statistics, sensor fusion, forecasting, and judgment
  calls about risk tolerance.
- The **pathfinding algorithm** answers: *"Given a graph where every edge already
  has a cost number, what's the cheapest way from A to B?"* — this is a pure,
  well-defined mathematical search problem with a known, exact, fast solution
  (Dijkstra/A*).

The fusion layer can get arbitrarily sophisticated (swap in a trained neural
forecaster, add new sensor types, tune risk weights) **without changing the
pathfinding algorithm at all**, as long as it keeps producing the same output
shape (`RoadEstimate` → a single edge cost number). This is exactly why RESQ's
existing `routing/engine.py::RouteGraph.shortest_path` accepts an arbitrary
`EdgeCost` callable rather than hardcoding how costs are computed — the seam
already exists in the code for this separation.

## What you should understand before reading the next file

You should be able to explain, without notes, why combining perception + ML +
routing + signals into "one AI model" would be a worse design than RESQ's current
separated-modules approach, and specifically what kind of problem each of the five
pipeline stages solves. Next: `20_ROUTING_ALGORITHM_COMPARISON.md`.
