# 21 — Evaluation Criteria for Selecting RESQ's Routing Approach

Before picking an algorithm (next file), these are the criteria that should
actually drive the decision, and why each one matters for *this specific* project
— not routing research in general.

## 1. Shortest travel time (as *correctness*, not just speed)

Matters because the entire point of the system is getting an ambulance to a
hospital faster. But it must mean "shortest according to the best available cost
estimate," not "shortest by naive distance" — see `01_ROUTING_PROBLEM.md`'s
warning against that beginner assumption.

## 2. ETA accuracy

Matters because ETA feeds the signal-preemption system (`PriorityRequest.eta_s`) —
a wrong ETA means the corridor controller prepares a green phase at the wrong time,
undermining the entire preemption benefit. An algorithm that finds the "shortest"
path using unrealistic edge costs is not actually useful even if it's
mathematically optimal *given those costs*.

## 3. Dynamic rerouting capability

Matters because conditions change while the ambulance is en route — a route chosen
at t=0 based on stale information can become wrong by t=60s. This is why
time-dependent costs (`algorithms/TIME_DEPENDENT_ROUTING.md`) and replanning
(`algorithms/D_STAR_LITE.md`) both matter, not just the base search.

## 4. Response to road blockage

Matters directly — RESQ's flagship demo scenario *is* a road blockage. An algorithm
family that can't cleanly express "this edge is now unusable" or "efficiently
recompute after a blockage" fails RESQ's core use case.

## 5. Traffic prediction integration

Matters because macro data alone is proven (in RESQ's own docs) to lag reality —
the whole justification for onboard perception is that "a recent accident may
still look clear in the macro traffic feed" (`docs/REVIEW_II_VIVA_NOTES.md` §1).
An algorithm that can't accept a *forecast* as part of its cost (not just current
conditions) can't benefit from prediction work at all.

## 6. Emergency priority (not treating the ambulance like ordinary traffic)

Matters because an ambulance's risk tolerance differs from a normal driver's — it
may reasonably prefer a route that's a little slower but much more certain, or one
that enables signal preemption more effectively. This is why a pure single-objective
(time-only) router is insufficient long-term, motivating
`algorithms/MULTI_OBJECTIVE_ROUTING.md`.

## 7. Computational latency

Matters because a slow router delays the *entire* decision chain — perception,
fusion, routing, and preemption all have to complete within the demo's 5 Hz update
budget (`api/main.py`'s `/ws/live` loop sleeps 0.2s between snapshots) or a
real-world equivalent latency budget. Exact classical algorithms
(Dijkstra/A*/D* Lite) comfortably meet this; approximate/learned methods add risk
here without benefit at RESQ's graph size.

## 8. Scalability (to a larger real map)

Matters because RESQ's current graph is a small bounding box
(`scripts/build_road_graph.py`'s `BBOX`); a credible path to a real deployment
needs an algorithm family that scales to a full city's road network without a
fundamental rework. Dijkstra/A*/D* Lite all scale in well-understood, predictable
ways (see complexity sections in their files); GA/ACO/RL do not offer a scaling
advantage here and add substantial engineering cost.

## 9. Deterministic behavior

Matters because RESQ's own experiment methodology depends on reproducibility —
`docs/REVIEW_II_VIVA_NOTES.md` explicitly states seeded, repeatable comparisons are
required for fair baseline evaluation (§5, "Why is reproducibility important?").
A non-deterministic pathfinder would undermine this same principle applied to the
routing decision itself.

## 10. Explainability

Matters for two concrete reasons specific to this project: (a) it's a student
research project that will be defended in a viva, where "why did it pick this
route" must have a clear answer; and (b) it's safety-adjacent — any real emergency
system needs to be auditable after the fact. Exact graph-search algorithms give a
transparent cost breakdown "for free"; metaheuristic/learned methods do not.

## 11. Implementation complexity

Matters practically — this is a student team project with finite time
(`docs/REVIEW_II_VIVA_NOTES.md` documents ongoing, incremental future work per
member). An algorithm that's dramatically harder to implement correctly
(D* Lite vs. Dijkstra) should only be adopted once its specific benefit (efficient
replanning) is actually needed, not adopted preemptively "because it's more
advanced."

## 12. Data requirements

Matters because RESQ's current data is largely staged/synthetic
(`sim/probes/generator.py`'s synthetic `VehicleTruth`, `experiments/runner.py`'s
formula-based metrics). Algorithms that *require* large amounts of real training
data (RL, to a lesser extent GA/ACO tuning) are a poor fit until RESQ has validated
real data pipelines (SUMO, real perception) — which the project's own docs mark as
future work.

## 13. Robustness

Matters because sensor/feed failures are explicitly modeled scenarios in RESQ
(`ScenarioName.STALE_FEED`, `ScenarioName.CAMERA_DEGRADED` in `schema/models.py`).
The chosen approach needs to degrade gracefully (e.g., fall back to macro-only
costs) rather than fail outright or behave unpredictably when an input is missing
or low-confidence.

## 14. Route stability

Matters because constantly flip-flopping between routes is dangerous and confusing
for a driver, and wastes any signal-preemption reservations already in progress.
RESQ already encodes this as a first-class concern
(`routing/engine.py::should_switch_route`'s hysteresis) — any richer cost function
must preserve, not undermine, this property.

## 15. Safety

Matters most of all — an emergency vehicle system's failure modes have real
consequences. This is why RESQ's signal controller is explicitly *not*
ML-controlled (`corridor/state_machine.py` docstring) and why this whole research
set repeatedly argues for keeping the pathfinding step exact/deterministic even
as the *inputs* to it get smarter.

## 16. Compatibility with signal preemption

Matters because routing and signal preemption are coupled through ETA — the
routing algorithm's output (`RouteOption`, implying arrival times at each
intersection) must map cleanly onto `PriorityRequest.eta_s`. An algorithm whose
output isn't easily expressed as "arrival time at each intersection along this
path" (e.g., a black-box learned policy that just outputs "turn left") would be
harder to integrate with the existing, working corridor controller.

## How these criteria will be used

`30_ROUTING_ALGORITHM_RECOMMENDATION.md` scores the realistic candidates against
these criteria explicitly, and explains — for each rejected algorithm — *which*
criteria it fails and why that failure matters enough to exclude it, rather than
picking a "best" algorithm by reputation or popularity.

## What you should understand before reading the next file

You should be able to pick any three criteria above and explain, using a specific
file or line from RESQ's own codebase, why that criterion is not hypothetical but
already reflected in a real design decision RESQ has made. Next:
`30_ROUTING_ALGORITHM_RECOMMENDATION.md`.
