# 60 — Viva Preparation: Routing & Architecture Questions

Organized by category. Each question has a beginner-friendly answer you should be
able to restate in your own words, not memorize verbatim.

## Beginner questions

**Q: What is a graph, in the context of this project?**
A: A collection of junctions (nodes) connected by road segments (edges), each with
a cost (travel time). RESQ's graph comes from a real OpenStreetMap extract of part
of Chennai.

**Q: What does "shortest path" mean here — shortest in distance?**
A: No — shortest in *travel time*, which RESQ computes as `length / assumed speed`
per edge. For an ambulance, time (and eventually risk/confidence) matters more than
raw distance.

**Q: What is a directed graph, and why does it matter for roads?**
A: A graph where edges can only be used in one direction — this models one-way
streets. RESQ's `RoadNetwork` builds directed edges based on OSM's `oneway` tag.

## Algorithm questions

**Q: What algorithm does RESQ currently use for routing?**
A: Dijkstra's algorithm, implemented twice — once directly in the Chennai demo road
graph (`sim/chennai/roads.py`), and once as a general, reusable, time-dependent
router (`routing/engine.py`).

**Q: What does "time-dependent" mean, precisely?**
A: The cost of using a road can depend on *what time you'd actually arrive at it*,
not just a fixed number — so the same route search can naturally account for "this
road will be worse in 10 minutes."

**Q: Why not use A\* instead of Dijkstra?**
A: A* isn't an alternative to Dijkstra, it's an enhancement of it — same
guarantees, just explores fewer irrelevant nodes using a distance-based heuristic
toward the destination. RESQ could add it without changing its core routing logic.

**Q: What is an admissible heuristic, and why does it matter?**
A: A heuristic that never overestimates the true remaining cost. If it did
overestimate, A* could skip over the actual shortest path and return a wrong
answer.

**Q: Why not use Bellman-Ford?**
A: Bellman-Ford solves a strictly harder problem (handles negative edge weights),
which RESQ never has (travel time is never negative), and it's slower than
Dijkstra for no benefit here.

**Q: What's the difference between D\* Lite and Dijkstra?**
A: Dijkstra finds a shortest path from scratch every time you run it. D* Lite finds
one once, then efficiently *repairs* it when the graph changes (e.g., a road gets
blocked), touching only the affected part of the graph instead of recomputing
everything.

## Architecture questions

**Q: Why does RESQ separate perception, prediction, fusion, routing, and signal
control into different modules instead of one big model?**
A: Because they solve fundamentally different kinds of problems (signal
processing, forecasting, statistics, graph search, safety-critical control), each
with different testing and correctness needs. Combining them would make the system
harder to test, harder to explain, and harder to safely change one part without
breaking another. RESQ's actual codebase already reflects this separation
(`perception/`, `forecast/`, `fusion/`, `routing/`, `corridor/` are independent,
independently-tested packages).

**Q: What is the "GOAT decision layer," concretely?**
A: That exact term doesn't appear anywhere in RESQ's code — it's a concept for
"the thing that combines multiple information sources into a routing decision." In
this project's real architecture, that job is split between the existing `fusion/`
module (combining redundant estimates of the same quantity) and a proposed
"dynamic edge-cost calculation" stage (combining different *kinds* of concern —
time, risk, confidence — into one number for the router).

**Q: Why does the signal controller never let ML/prediction directly change a
signal?**
A: Safety. `corridor/state_machine.py`'s own docstring states this design rule
explicitly. Prediction can *request* priority, but the deterministic state machine
enforces minimum green, amber, all-red, and recovery timings regardless — this
guarantees `conflicting_green` is always `False`, which is a hard safety
requirement, not a tunable preference.

## Comparison questions

**Q: How would you compare Dijkstra and Genetic Algorithms for this problem?**
A: Dijkstra finds the *exact* optimal route in milliseconds on RESQ's graph size,
deterministically, with a fully explainable cost breakdown. A GA would only
*approximate* an answer, non-deterministically, after evaluating many candidate
routes over many generations — strictly worse on correctness, speed, and
explainability for a problem this well-structured and this small.

**Q: When would a Genetic Algorithm or Ant Colony Optimization actually make
sense?**
A: On large combinatorial problems where no efficient exact algorithm is known —
e.g., coordinating routes for many vehicles to many stops at once. RESQ's actual
query (one ambulance, one destination) isn't that kind of problem.

**Q: Isn't Reinforcement Learning more "advanced" — why not use it for extra
credit?**
A: "More advanced" isn't the same as "more appropriate." RL is built for
sequential decision problems learned from experience — a poor fit when an exact,
fast, auditable algorithm (Dijkstra) already solves the underlying problem
perfectly. Using RL here would trade away correctness guarantees and
explainability for no real benefit. RL has a more legitimate role elsewhere (e.g.,
tuning a signal-preemption policy), but even there, it should never replace the
deterministic safety state machine.

## Why-this-algorithm questions

**Q: Why is explainability such a big deal for a student project?**
A: Two reasons: you need to defend design choices in the viva with clear
reasoning, and any real emergency system's decisions may need to be audited after
an incident. An exact graph-search algorithm gives you a transparent cost
breakdown "why this route" for free; a trained model or metaheuristic does not.

**Q: Why does route "stability" matter, and how does RESQ already handle it?**
A: Switching routes back and forth due to small noisy differences is dangerous and
confusing, and wastes any in-progress signal-preemption setup. RESQ's
`should_switch_route` requires a candidate route to save both ≥10 seconds *and*
≥5% before switching — a hysteresis threshold.

## Failure-case questions

**Q: What happens if the onboard camera fails?**
A: Perception goes offline; RESQ's health reporting (`DataHealth.perception_online`)
reflects this, and overall confidence drops. The route can still be computed from
macro data alone, but a fresh local incident might not be detected — this is
explicitly modeled as the `CAMERA_DEGRADED` scenario.

**Q: What if two estimates for the same road strongly disagree — which one wins?**
A: Neither "wins" outright by default — `fuse_observations` combines them by
inverse-variance weighting (more confident/less noisy estimates get more
influence), *except* for one deliberate hard rule: a high-confidence (≥0.8)
perception blockage report overrides everything and forces the road to a
near-closed cost, because a real, nearby, confident blockage report shouldn't be
"averaged away" by a possibly-stale macro estimate.

**Q: What if a road becomes blocked while the ambulance is already using it?**
A: Not currently simulated mid-transit in the live demo (today's demo picks a route
at the start of each run), but architecturally this is exactly the scenario D* Lite
targets — efficiently updating the plan for the *remaining* trip without discarding
already-valid parts of the route.

## Complexity questions

**Q: What's the time complexity of Dijkstra, and does it matter for RESQ?**
A: O((V+E) log V) with a binary heap. At RESQ's current small graph size (a few
hundred nodes), this runs in a fraction of a millisecond — complexity is a
non-issue today, but matters if RESQ's map is ever expanded to full-city scale.

**Q: Does A\* have better worst-case complexity than Dijkstra?**
A: Not in Big-O terms — both are O((V+E) log V) worst case. A*'s benefit is
*practical*: with a good heuristic it typically visits far fewer nodes in practice,
even though the worst-case bound doesn't improve.

## ML + routing integration questions

**Q: Where would a trained congestion-prediction model plug into RESQ?**
A: As a drop-in replacement for `forecast/models.py`'s `SpatialTemporalForecaster`,
producing the same `SpeedForecast`-shaped output. Nothing downstream (fusion,
routing, signals) would need to change, because they only depend on that output
shape, not on how it was produced.

**Q: Could you use a neural network to directly output a route?**
A: You could try, but you'd lose Dijkstra's optimality guarantee and
explainability for no clear benefit, since RESQ's graph is small enough that exact
search is already fast. The better use of ML is *upstream* — predicting the inputs
(congestion, blockage likelihood) that feed a classical, auditable router.

## YOLO + routing questions

**Q: Does YOLO output feed directly into the router?**
A: No — YOLO (or any detector) would feed `perception/pipeline.py`, producing
`TrackedObject`s, which `BlockageDetector` turns into a `RoadObservation`. That
observation goes through fusion before ever becoming part of an edge cost the
router sees. The router never sees raw detections.

## Traffic prediction questions

**Q: What's the difference between RESQ's two forecasting baselines?**
A: `HistoricalAverageForecaster` predicts a road's speed from its historical
average at the same time-of-week bucket. `SpatialTemporalForecaster` blends a
road's own recent trend with its neighbors' current speeds using a persistence
factor — a simple way to account for congestion "spreading" spatially without a
trained model.

**Q: Why are these called "baselines" and not the final system?**
A: They're explicitly simple and explainable so the system works end-to-end before
a trained model is introduced — `forecast/models.py`'s own docstring says they're
"used before GPU graph models are introduced." They also give a reference point:
any future trained model should be shown to beat these baselines, not just assumed
to be better.

## Signal-preemption questions

**Q: Walk me through the signal state machine's phases.**
A: NORMAL (default) → REQUEST_VALID (a priority request received) → PRE_CLEAR (hold
minimum green for current traffic) → SAFE_TRANSITION (amber + all-red to clear the
intersection) → EV_SERVICE (green for the ambulance) → EV_PASSED (ambulance has
cleared) → RECOVERY (let delayed cross-traffic catch up) → back to NORMAL.

**Q: Why is there a mandatory all-red phase?**
A: To guarantee no vehicle is still legally entering the intersection on a stale
green before the ambulance's approach gets service — this is what keeps
`conflicting_green` false; it's a hard safety timing, not something the routing or
prediction layer can skip.

**Q: What is "queue-debt recovery" and why does it matter?**
A: After a preemption event, cross-traffic that was held back gets a recovery
service period so their accumulated wait isn't just... forgotten. Baseline B5
models this explicitly and shows lower net cross-traffic delay than fixed
preemption (B2/B3) in RESQ's synthetic experiment numbers.

## What you should understand before you're done

If any answer above feels like it's reciting rather than reasoning, go back to the
file it's drawn from (cross-referenced by keyword) and re-derive the answer from
the underlying concept before your viva.
