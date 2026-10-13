# 30 — Recommendation

> Current review status: Dijkstra and A* are implemented and measured. Replanning
> uses fresh A* searches. D* Lite remains future work until an implementation can
> be demonstrated and benchmarked. The discussion below is the original design
> recommendation, not evidence that all proposed components exist.

This is written only after completing `01`–`21`. It answers the brief's 15 explicit
questions directly.

## 1. What routing algorithm should RESQ use?

**Time-dependent Dijkstra as the core pathfinding algorithm** (already present as
`routing/engine.py::RouteGraph.shortest_path`), enhanced with an **A\*** heuristic
for search speed, and extended with **D\* Lite**-style incremental replanning for
efficient reaction to blockages/incidents. The edge costs it consumes should come
from a **weighted-cost (scalarized) combination** of fused travel time, confidence,
and risk (`algorithms/MULTI_OBJECTIVE_ROUTING.md`), not from a new "algorithm" but
from richer inputs to the same exact search.

This is not a single-algorithm swap — it is "keep the exact, deterministic search
core RESQ already built; make it faster (A\*); make it react efficiently to change
(D\* Lite); make its inputs richer (time-dependent, multi-objective costs)."

## 2. Why?

Because `21_ROUTING_EVALUATION_CRITERIA.md`'s criteria — correctness, latency,
determinism, explainability, safety, compatibility with the existing signal system
— are all best satisfied by exact classical graph search, and RESQ's own codebase
already independently arrived at Dijkstra twice (`sim/chennai/roads.py`,
`routing/engine.py`), which is strong evidence this is the right shape of solution
for this problem, not just a textbook default.

## 3. Why not Dijkstra (plain, unmodified)?

Plain Dijkstra *is* part of the recommendation — it's not rejected, it's the
foundation. What's added on top (time-dependent costs, D* Lite replanning) are
extensions, not replacements. If the question means "why not stop at plain,
static-weight Dijkstra": because it cannot express costs that vary by
predicted/actual arrival time, so it can't use forecasts at all — failing
evaluation criterion #5 (traffic prediction integration).

## 4. Why not A\* (as the *only* change)?

A* alone is also part of the recommendation, not rejected — it is a pure speed
improvement over Dijkstra with identical guarantees. "Why not *only* A*, without
D* Lite or time-dependence" — because A* alone still doesn't solve efficient
replanning (criterion #3/#4) or predictive costing (criterion #5); it only makes
each individual search faster.

## 5. Why not D\* Lite (as the *only* change)?

Also part of the recommendation. If the question is "why not D* Lite instead of
Dijkstra/A*" — it isn't instead of; D* Lite is built *on top of* A*-style search.
It specifically should not be the *first* thing implemented, though, because of its
higher implementation complexity (criterion #11) relative to its benefit at RESQ's
*current* small, mostly-static demo graph — see `40_ROUTING_IMPLEMENTATION_PLAN.md`
for a staged rollout that defers D* Lite until it's genuinely needed (larger map,
frequent live updates).

## 6. Why not time-dependent routing (as the *only* change)?

Also part of the recommendation (it already exists in `routing/engine.py`). Not
sufficient *alone* because it doesn't solve efficient replanning (still requires a
fresh search per query) or multi-objective tradeoffs (still assumes one scalar
cost) — it needs to be combined with the cost-model work in
`10_GOAT_DECISION_ENGINE.md` to be useful, and ideally with D* Lite for efficiency
at scale.

## 7. Why not genetic algorithms?

Rejected for the pathfinding step (classification **C**, see
`algorithms/GENETIC_ALGORITHM.md`): no optimality guarantee where an exact,
fast method already exists; non-deterministic, undermining reproducibility
(criterion #9); low explainability (criterion #10), which matters for both the
viva and any future safety review; and it solves a different problem shape (large
combinatorial search) than RESQ's single-vehicle, single-query problem.

## 8. Why not ant colony optimization?

Rejected for the same core reasons as genetic algorithms
(`algorithms/ANT_COLONY_OPTIMIZATION.md`): approximate, non-deterministic, wrong
problem shape, and strictly more computation than exact search for no benefit at
RESQ's graph size.

## 9. Why not reinforcement learning?

Rejected specifically **for the pathfinding step**
(`algorithms/REINFORCEMENT_LEARNING_ROUTING.md`): exact search already solves this
sub-problem optimally and fast; RL needs training data/simulation RESQ doesn't yet
have validated (its own docs mark SUMO/trained-model work as future); and a
learned policy is far harder to audit than a transparent edge-cost sum — directly
conflicting with RESQ's own stated design principle that safety-relevant decisions
stay deterministic (`corridor/state_machine.py`'s "ML never owns a signal
transition"). RL is *not* rejected wholesale — see Q13.

## 10. Should RESQ use one algorithm or a combination?

**A combination — but of components, not competing pathfinding algorithms.**
Exactly one pathfinding algorithm family (Dijkstra/A*/D* Lite, which are the same
family at different levels of enhancement) should own route search. Separately,
different techniques belong in different pipeline stages: statistical/forecasting
methods in prediction, deterministic rule-based logic in fusion and signal
preemption, and (potentially, later) learned models feeding *inputs* to the cost
model — never replacing the search itself. This is the core argument of
`10_GOAT_DECISION_ENGINE.md`.

## 11. What should the GOAT/decision layer do?

Combine perception, macro/prediction data, and confidence into a single, per-edge,
possibly time-varying **cost number** (or a small set of clearly-labeled cost
terms for a weighted-sum), and hand that to the pathfinder. It should **not**
itself decide the route — that's the pathfinder's job — and it should **not**
touch signal state directly — that's the corridor controller's job, reacting to
routing's output.

## 12. What should the routing algorithm do?

Take a graph and a (possibly time-dependent, possibly multi-term) edge-cost
function, and return the exact cheapest path and its cost — nothing more. It should
not know about sensors, confidence sources, or signal states; it consumes numbers,
not raw data.

## 13. What should the traffic prediction model do?

Produce a `SpeedForecast`-shaped output (a predicted speed/travel-time and
uncertainty for a road, at a future time horizon) that fusion can combine with
perception data — currently the explainable baselines in `forecast/models.py`; in
future, potentially a trained model (GNN or similar), swapped in *without changing
any downstream module*, because the interface (predicted speed + uncertainty per
road per horizon) stays the same. This is exactly the kind of place RL/learned
methods have a legitimate, non-pathfinding role (see Q9's caveat).

## 14. What should YOLO/perception do?

Convert raw sensor frames into `TrackedObject`s (class, lane, distance, speed,
stopped-duration, confidence), which `perception/pipeline.py`'s existing
`BlockageDetector` and prediction logic already know how to consume. Perception
should never talk to routing or signals directly — only through the `RoadObservation`
contract into fusion.

## 15. What should the signal-preemption layer do?

Exactly what `corridor/state_machine.py` already does: own the deterministic,
safety-invariant-respecting state machine (minimum green, amber, all-red, max
preemption, recovery). It should *consume* `PriorityRequest`s (derived from
routing's ETA output) but must never be short-circuited by a prediction or a
routing decision — safety transitions stay rule-based, full stop.

## The overall architecture question

**Prediction → Fusion → Dynamic Edge-Cost Calculation → Routing → Signal
Coordination** is the correct shape, and it is *not* a hypothetical structure
invented for this document — it is what RESQ's actual module boundaries
(`perception/`, `forecast/`, `fusion/`, `routing/`, `corridor/`) already encode,
just not yet fully wired end-to-end on the live demo path (see
`00_PROJECT_UNDERSTANDING.md` §6 and `40_ROUTING_IMPLEMENTATION_PLAN.md` for the
concrete gap). The next file draws this out as an explicit diagram.

## What you should understand before reading the next file

You should be able to answer all 15 numbered questions above in your own words,
without re-reading this file, before moving to `31_FINAL_ROUTING_ARCHITECTURE.md`.
