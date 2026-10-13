# 20 — Fair Comparison of Realistic Routing Algorithm Candidates

This compares the **A and B classified candidates** from `02_ALGORITHM_CANDIDATES.md`
(Dijkstra, A*, Bidirectional search, D* Lite, Time-dependent routing,
Multi-objective/weighted-cost routing), plus brief columns for the eliminated
C-candidates for completeness. All are PATHFINDING-component algorithms except
where noted (time-dependent and multi-objective routing are cost-model extensions
applied to the same pathfinding core).

## Comparison table

| Algorithm | Basic idea | Static traffic | Dynamic traffic | Road blockage | Prediction input | Real-time suitability | Optimality | Implementation difficulty | Explainability | Memory usage | Replanning | Emergency suitability | RESQ compatibility | Important limitations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Dijkstra** | Expand cheapest known frontier node first | Yes | Via rerun | Yes (exclude edges) | No (needs time-dependent extension) | Excellent at RESQ scale | Exact | Low | Very high | Low, O(V+E) | Full rerun only | High | **Already implemented (2×)** | Rerun-only replanning; no built-in sense of direction |
| **A\*** | Dijkstra + goal-directed heuristic | Yes | Via rerun | Yes | No (needs extension) | Excellent, better than Dijkstra at scale | Exact (if heuristic admissible) | Low-moderate | Very high | Low, O(V+E) | Full rerun only | High | Drop-in over existing Dijkstra code | Needs a correct admissible heuristic |
| **Bidirectional Dijkstra/A\*** | Search from both ends, meet in middle | Yes | Via rerun | Yes | No | Good, more benefit at larger scale | Exact | Moderate-high | High | ~2× single-direction, needs reverse graph | Full rerun only | High | Needs new reverse-adjacency structure | Time-dependent combination is non-trivial |
| **D\* Lite** | Incrementally repair a shortest-path search when the graph changes | Yes | Yes, natively | Yes, efficiently, without full rerun | No (separate concern) | Excellent for streaming updates | Exact | High | High (final path); moderate (process) | O(V+E) + persistent state | **Purpose-built for this** | Very high, matches RESQ's incident use case | Requires persistent search state, bigger change to demo's compute-once pattern | Most complex to implement correctly of the classical candidates |
| **Time-dependent routing** (modifier on Dijkstra/A*) | Edge cost is a function of arrival time, not a constant | Yes (degenerates to static) | **Yes, this is its purpose** | Yes (cost can go to ∞ at a given time) | **Yes — this is exactly how forecasts get used** | Excellent, same as base algorithm | Exact, given FIFO property | Low (already implemented in `routing/engine.py`) | High | Same as base algorithm | Recompute per query; combine with D* Lite for incremental | Very high — the mechanism that connects forecasting to routing | Needs FIFO-respecting cost functions; naive combination with bidirectional search is tricky |
| **Multi-objective / weighted-cost routing** (modifier on Dijkstra/A*/D* Lite) | Combine several concerns (time, risk, confidence, stability) into one scalar edge cost | Yes | Yes (combines with time-dependence) | Yes (risk term, or hard exclusion) | Yes (confidence/risk terms can come from prediction) | Excellent (scalarized; no algorithmic overhead) | Exact for the *combined* cost, not necessarily "true" on any single raw objective | Low-moderate (cost-combining function + weight config) | High, if each term is reported separately | Same as base algorithm | Same as base algorithm | Very high — the mechanism that makes safety/confidence tradeoffs explicit | Extends `fusion/engine.py`'s pattern one level up | Weight choice is a real design decision requiring justification |
| Bellman-Ford *(eliminated, D)* | Relax all edges repeatedly, V-1 times | Yes | Via rerun | Yes | No | Poor — much slower than Dijkstra for no benefit | Exact | Low | High | O(V+E) | Full rerun only | Low (no advantage, real cost) | Solves a problem RESQ doesn't have (negative weights) | O(VE) — far slower than Dijkstra here |
| Floyd-Warshall *(eliminated, D)* | Compute all pairs' shortest paths at once | Yes | Via rerun (expensive) | Yes | No | Poor for RESQ's single-query need | Exact | Low | High | O(V²) | Full rerun only | Low | Wasteful — RESQ never needs all-pairs | O(V³) time, O(V²) memory — huge overkill |
| Yen's K-shortest paths *(B, secondary)* | Repeatedly re-run shortest-path with top choices excluded to get K ranked alternatives | Yes | Via rerun | Yes | No | Good at RESQ scale | Exact per alternative | Moderate | High | K × base algorithm's memory | Full rerun only | Medium — nice-to-have for showing route options | Builds directly on existing Dijkstra core | K× the base algorithm's cost; not core, but easy UI value-add |
| Genetic Algorithm *(eliminated, C)* | Evolve a population of candidate routes | Approximate | Approximate | Approximate (as a soft penalty) | Could encode, awkwardly | Poor | No guarantee | Moderate-high | Low | Depends on population size | Re-run population, slow | Low — non-deterministic, unauditable | Not recommended | Wrong problem shape; unnecessary given exact methods available |
| Ant Colony Optimization *(eliminated, C)* | Simulated ants reinforce good paths via pheromone | Approximate | Approximate | Approximate | Could encode, awkwardly | Poor | No guarantee | Moderate-high | Low | Depends on ant count/rounds | Re-run colony, slow | Low | Not recommended | Wrong problem shape; unnecessary given exact methods available |
| Reinforcement Learning *(eliminated, C, for pathfinding)* | Learn a policy from trial-and-error reward signals | Needs training data | Needs training data | Needs training data/careful reward design | Learned implicitly | Fast at inference *if* well-trained; poor to develop | No guarantee | High (training infra, data, tuning) | Low | Model-dependent | Needs retraining or online learning | Low for pathfinding; possible elsewhere (signal policy tuning) | Not recommended for pathfinding; possible future role elsewhere | Needs simulation/data RESQ doesn't yet have; hard to audit |

## Explanation, in plain English

**Dijkstra and A\* are not competitors to pick between — A\* is a speed
enhancement on top of Dijkstra that keeps the exact same guarantees.** RESQ already
has Dijkstra implemented twice; adding an A* heuristic is a natural refinement, not
a replacement, and matters more as RESQ's map grows.

**Bidirectional search is a further speed technique**, valuable once the graph is
large enough that halving each direction's search radius meaningfully helps —
not urgent at RESQ's current small demo scale, and it introduces real complexity
around time-dependent costs that should be weighed against the benefit.

**D\* Lite solves a different problem than raw search speed: it solves "efficient
replanning."** This is the single most relevant *specific* algorithm to RESQ's
headline demo scenario (a hidden incident appearing mid-journey), because it's
built exactly for "the graph just changed a little, update the plan without
starting over."

**Time-dependent routing and multi-objective/weighted-cost routing are not
separate algorithms at all — they are *modifications to the cost function* that
Dijkstra/A*/D* Lite already support**, because RESQ's own `routing/engine.py`
was designed with a pluggable `EdgeCost` callable from the start. This is why
they appear as full-fledged rows here even though, mechanically, they don't change
the search loop — what they change is *what number the search loop sees per edge*,
which is exactly where forecasting, fusion, and risk-tolerance decisions belong.

**Bellman-Ford and Floyd-Warshall are strictly worse choices for RESQ** — they
solve problems RESQ doesn't have (negative weights; all-pairs paths) at real
computational cost, with no upside.

**Yen's K-shortest paths is a reasonable, low-risk addition** if RESQ wants to show
the driver/dispatcher more than one ranked alternative route (matching the existing
`RouteOption` list concept in `schema/models.py`), built directly on the existing
Dijkstra core with no new algorithm family.

**Genetic Algorithm, Ant Colony Optimization, and Reinforcement Learning are not
"worse Dijkstra" — they are tools for different problem shapes** (large
combinatorial search, or learned sequential policies) that RESQ's single-vehicle,
single-query, exactly-solvable pathfinding problem does not need, and their
non-determinism and low explainability are actively undesirable for a
safety-critical, review-facing system.

## What you should understand before reading the next file

You should be able to point at this table and explain, for any row, whether it
solves the *same* problem as Dijkstra (a speed or replanning variant) or a
*different* problem (multi-stop combinatorial search, or a learned policy) — that
distinction is what separates the A/B candidates from the eliminated C/D ones.
Next: `21_ROUTING_EVALUATION_CRITERIA.md`.
