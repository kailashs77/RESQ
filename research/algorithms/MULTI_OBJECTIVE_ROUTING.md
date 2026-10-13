# Multi-Objective / Weighted-Cost Routing

**Status in RESQ: Candidate algorithm — not currently implemented as a general
mechanism.** RESQ's `fusion/engine.py` already combines *two data sources* into one
number for one objective (travel time); this file is about combining *several
different kinds of concern* (time, safety, confidence, stability) into the cost the
pathfinder optimizes.

## 1. One-sentence definition

Multi-objective routing is what you do when "best route" depends on more than one
thing at once (time, risk, confidence...) that don't naturally reduce to a single
number on their own — you must explicitly decide how to combine them.

## 2. What problem does it solve?

Plain Dijkstra/A* minimize *one* number per edge. But `01_ROUTING_PROBLEM.md`
established that an ambulance route should weigh time, safety/blockage risk,
confidence of the estimate, and route stability. If you feed the pathfinder several
*separate* numbers, it doesn't know how to compare "3 seconds faster but 40% less
certain" against "a bit slower but much more certain." Multi-objective routing
techniques solve *how to make that tradeoff explicit and principled*.

## 3. Terms I need to know

- **Objective**: one thing you care about and want to minimize/maximize (e.g.,
  travel time; another example: risk).
- **Pareto-optimal (non-dominated) path**: a path where no other path is *at least
  as good on every objective and strictly better on at least one*. There can be
  many Pareto-optimal paths (e.g., one that's faster-but-riskier, another
  slower-but-safer), and none of them is "wrong" — the choice between them is a
  value judgement, not a computation.
- **Pareto frontier**: the full set of Pareto-optimal paths.
- **Scalarization / weighted-sum cost function**: the practical shortcut of
  combining multiple objectives into **one number** using weights, e.g.
  `cost = w1*time + w2*risk + w3*(1-confidence)`, so an ordinary single-objective
  algorithm (Dijkstra/A*) can be used unchanged. This trades away "explore every
  tradeoff" for "get one clear, fast, explainable answer" — which is what a
  real-time ambulance decision actually needs.
- **Lexicographic ordering**: an alternative combination rule — optimize objective
  1 first; among ties, use objective 2 to break them; and so on. Useful when one
  objective (e.g., "never use a blocked road") is a hard constraint, not a
  tradeoff — RESQ already does this implicitly by *removing* blocked edges rather
  than penalizing them.

## 4. Basic idea

There are two broad approaches:

**(a) True multi-objective search**: extend Dijkstra/A* to track a *set* of
non-dominated partial paths per node instead of a single "best" cost, producing the
full Pareto frontier. Powerful, but significantly more complex and slower (the
number of Pareto-optimal paths can grow quickly), and its output (a *set* of
routes) requires a further decision about which one to actually drive.

**(b) Scalarization (the practical, recommended approach for RESQ)**: decide, in
advance, how much each objective matters (weights), combine them into **one** edge
cost number, and run ordinary Dijkstra/A*/D* Lite on that combined cost — unchanged
algorithm, richer cost.

RESQ's own architecture already leans toward (b): `fusion/engine.py` computes one
`RoadEstimate.travel_time_s` and one `confidence` per road, which is exactly the
"combine several signals into one number" move — just currently scoped to
*travel-time estimation*, not the full route-cost decision (see
`10_GOAT_DECISION_ENGINE.md` for how to extend this).

## 5. Tiny example

Suppose road A→C is fast (2s) but has a 30% chance of a recently-reported hazard
(judged from lower confidence), and A→B→D is slower (9s total) but fully confident.

A pure-time router picks A→C→D (3s) blindly. A weighted-cost router with, say,
`cost = travel_time + 20 * (1 - confidence)` might compute:
- A→C→D: `3 + 20*(1-0.70) = 3 + 6 = 9`
- A→B→D: `9 + 20*(1-0.98) = 9 + 0.4 = 9.4`

Now the two routes are nearly tied — the weighted cost makes the *safety/confidence
tradeoff explicit and inspectable* instead of hiding it, and a small change in the
weight (say, valuing confidence more for an ambulance) could flip the decision in a
principled, explainable way.

## 6. How the algorithm chooses a route

Under scalarization: exactly the same as Dijkstra/A* — the algorithm doesn't even
know it's "multi-objective"; it just receives a single already-combined number per
edge. All the "multi-objective" thinking happens in *how that number is
constructed* (the cost model / fusion layer), not in the search algorithm itself.
This is precisely why the brief's four-component breakdown
(`02_ALGORITHM_CANDIDATES.md`) separates COST MODEL from PATHFINDING.

## 7. What happens if traffic changes?

Combines naturally with time-dependent routing (`TIME_DEPENDENT_ROUTING.md`): the
weighted-cost function can itself be time-dependent, e.g. `cost(edge, t) =
w1*travel_time(edge, t) + w2*risk(edge, t) + w3*(1 - confidence(edge, t))`.

## 8. What happens if a road becomes blocked?

Two valid designs, and RESQ should pick deliberately, not accidentally:
- **Hard constraint** (RESQ's current approach): remove the edge from the graph
  entirely. Correct when confidence is very high (this is why
  `fusion/engine.py`'s blockage override uses a near-infinite cost of 3600s rather
  than literal removal — functionally almost the same, but keeps the edge
  theoretically usable as an absolute last resort if literally no other path
  exists).
- **Soft, heavily-weighted cost**: useful when blockage confidence is moderate, so
  the router can still use the road if every alternative is drastically worse, but
  will strongly prefer not to.

## 9. What inputs would RESQ provide?

Per edge, per (predicted) time: fused travel time, a confidence value, a
blockage/risk indicator, and (optionally) a route-stability penalty (to discourage
constant switching, complementing the existing hysteresis rule in
`should_switch_route`). Weights (`w1, w2, w3, ...`) would be a configuration
decision made by RESQ's designers, ideally documented and justified, not a hidden
magic number.

## 10. What output would RESQ receive?

If scalarized (recommended): identical output shape to plain Dijkstra/A* — one best
route by combined cost, still fully explainable if RESQ also reports each
objective's contribution (e.g., "9.4s total: 9s travel time + 0.4 confidence
penalty").

## 11. Advantages

- Makes real tradeoffs (time vs. safety vs. confidence) explicit and adjustable,
  instead of ignoring them (pure time) or hardcoding one special case (the current
  blockage-override hack).
- Scalarization keeps all of Dijkstra/A*/D* Lite's speed, exactness, and
  explainability — no new pathfinding algorithm needed.
- Weights can be tuned/justified/documented for a viva or safety review, unlike an
  opaque learned combination.

## 12. Disadvantages

- Choosing weights is a real design decision with real consequences — done poorly,
  it can hide bad tradeoffs behind a single misleading number.
- True Pareto-frontier search (option a) is significantly more complex and returns
  a set of routes, not one decision — someone/something still has to pick one, so
  it doesn't actually avoid the weighting decision, just defers it.
- Different objectives often use different units (seconds vs. a 0–1 confidence
  score) — combining them requires a deliberate normalization, or the weights
  implicitly absorb unit conversion in a way that's easy to get wrong.

## 13. Computational complexity

Scalarization: no additional complexity beyond whichever base algorithm is used
(Dijkstra/A*/D* Lite) — the combination happens once per edge evaluation, an O(1)
extra computation. True Pareto search: significantly higher — the number of
maintained non-dominated paths per node can grow substantially, making it
considerably slower and more memory-hungry; generally impractical for a real-time
single-vehicle system like RESQ.

## 14. Real-time suitability

Excellent for scalarization (same as the base algorithm); poor for full
Pareto-frontier search at real-time, single-query scale — another reason
scalarization is the right choice for RESQ specifically.

## 15. Emergency ambulance suitability

High for scalarization — it is exactly the mechanism needed to make "route around
what's fast AND what's safe AND what's confidently known" a real, principled
decision rather than an ad hoc one.

## 16. Explainability

High, if done carefully: report each objective's raw value and its contribution to
the final cost (as in the tiny example above), not just the final number. This is
strictly better for explainability than RESQ's current single hardcoded blockage
override, which is a special case rather than a general, inspectable rule.

## 17. Implementation difficulty

Low-to-moderate for scalarization (a cost-combining function plus configuration for
weights); high for true Pareto search (a different, more complex algorithm family)
— another reason to prefer scalarization for RESQ.

## 18. How it fits the existing RESQ architecture

This is the generalization of what `fusion/engine.py` already does for *travel
time specifically* (combining macro + perception into one number). Multi-objective
scalarization would extend that pattern **one level up**: combining travel time +
confidence + risk + stability into the single edge cost handed to
`routing/engine.py::RouteGraph.shortest_path`'s `edge_cost` parameter — the exact
seam that already exists in the code.

## 19. Where it would sit in the pipeline

Primarily the **COST MODEL** component, feeding into PATHFINDING — this is the
clearest example in this whole research set of why the four-component separation
matters: multi-objective *thinking* happens before the search algorithm runs, not
inside it.

## 20. Simple pseudocode

```text
function combined_edge_cost(edge, clock_time, weights):
    fused = fuse_observations(edge.road_id, macro_obs, perception_obs)   # existing
    forecast = predict(edge.road_id, clock_time)                          # existing/planned
    risk = 1.0 if fused.blockage else 0.0
    instability_penalty = stability_cost(edge, current_route)             # new, optional

    return (
        weights.time      * fused.travel_time_s
        + weights.risk     * risk
        + weights.confidence * (1 - fused.confidence)
        + weights.stability  * instability_penalty
    )

# then: RouteGraph.shortest_path(start, goal, edge_cost=combined_edge_cost)
```

## 21. What I should remember for viva

- "Multi-objective" does not require a new pathfinding algorithm — **scalarization**
  (weighted sum into one cost) lets Dijkstra/A*/D* Lite handle it unchanged.
- True Pareto-frontier search exists but is the wrong tool for RESQ's real-time,
  single-decision use case.
- RESQ already does a small-scale version of this idea in `fusion/engine.py`
  (inverse-variance combination of two *sources* for *one* objective); the
  extension here is combining several different *kinds* of objective.
- Weights are a design decision that should be explicit, documented, and
  justifiable — not hidden.

## 22. One practical example

Today, RESQ's blockage override is a hardcoded special case: "if confidence ≥ 0.8,
force travel time to 3600s." A weighted-cost design would instead let *any* level
of confidence and risk smoothly influence the edge cost, with the current override
becoming just one (very reasonable) extreme point on a continuous, tunable scale —
more general, and easier to justify to a reviewer asking "why 0.8, why exactly
3600?"

## What you should understand before reading the next file

You should be able to explain the difference between a true Pareto-frontier search
and scalarization, and say which one RESQ should use and why. Next: read
`10_GOAT_DECISION_ENGINE.md`, which builds directly on this file's ideas to propose
a concrete cost-function design for RESQ.
