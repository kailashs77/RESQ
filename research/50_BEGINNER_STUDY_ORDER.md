# 50 — Beginner Study Roadmap

Read in this exact order. Each step lists what to understand before moving on,
keywords you should recognize, and what you should be able to explain in your own
words before continuing.

## Step 1 — `00_PROJECT_UNDERSTANDING.md`
**Before continuing you should understand**: what RESQ actually does today vs.
what's only planned; the two separate routing code paths that exist.
**Keywords**: implemented / planned / staged, fusion, perception, corridor.
**You should be able to explain**: the difference between `sim/chennai/roads.py`'s
router and `routing/engine.py`'s router, in your own words.

## Step 2 — `03_GRAPH_THEORY_FOR_RESQ.md`
**Before continuing you should understand**: node, edge, directed graph, weighted
graph, path, adjacency.
**Keywords**: graph, node, edge, weight, adjacency list, directed, one-way.
**You should be able to explain**: how OSM `<way>` data becomes RESQ's adjacency
dictionary, and trace a shortest path by hand on the 4-node example.

## Step 3 — `01_ROUTING_PROBLEM.md`
**Before continuing you should understand**: why "shortest distance" is the wrong
mental model; static vs. dynamic vs. time-dependent routing; constraint vs. cost.
**Keywords**: edge weight, ETA, static routing, dynamic routing, time-dependent
routing, heuristic, cost function, constraint.
**You should be able to explain**: at least three things (besides distance) an
ambulance route should account for, and whether RESQ currently supports each.

## Step 4 — `02_ALGORITHM_CANDIDATES.md`
**Before continuing you should understand**: the four-component breakdown
(pathfinding / cost model / fusion / signal preemption); which algorithms were
eliminated early and why.
**Keywords**: A/B/C/D classification, pathfinding, cost model.
**You should be able to explain**: why Bellman-Ford/Floyd-Warshall don't fit, and
why GA/ACO/RL are not being used as the pathfinding algorithm.

## Step 5 — `algorithms/DIJKSTRA.md`
**Before continuing you should understand**: the core Dijkstra loop; why it's
exact; its complexity.
**Keywords**: priority queue, relaxation, frontier, non-negative weights.
**You should be able to explain**: the algorithm from memory, in pseudocode or
plain English, and point to its implementation in RESQ.

## Step 6 — `algorithms/A_STAR.md`
**Before continuing you should understand**: how A* differs from Dijkstra; what
"admissible heuristic" means.
**Keywords**: heuristic, admissible, consistent, f(n)=g(n)+h(n).
**You should be able to explain**: why an inadmissible heuristic can break
correctness.

## Step 7 — `algorithms/BIDIRECTIONAL_SEARCH.md`
**Before continuing you should understand**: why knowing both endpoints in advance
enables this technique; its complication with time-dependent costs.
**Keywords**: forward search, backward search, meeting point, reverse graph.
**You should be able to explain**: why this wouldn't help "find the nearest
hospital of any kind" (unknown destination).

## Step 8 — `algorithms/D_STAR_LITE.md`
**Before continuing you should understand**: the difference between recomputing a
route and repairing one; how this maps to RESQ's incident scenario.
**Keywords**: incremental search, LPA*, inconsistent node, replanning.
**You should be able to explain**: why RESQ's current `blocked=frozenset(...)`
rerun approach works but doesn't scale, and what D* Lite would change.

## Step 9 — `algorithms/TIME_DEPENDENT_ROUTING.md`
**Before continuing you should understand**: edge cost as a function of arrival
time; the FIFO property.
**Keywords**: time-dependent, FIFO property, arrival-time function.
**You should be able to explain**: why the same graph can have a different optimal
route depending on departure time, with a concrete example.

## Step 10 — `algorithms/MULTI_OBJECTIVE_ROUTING.md`
**Before continuing you should understand**: Pareto-optimality vs. scalarization;
why RESQ should scalarize.
**Keywords**: Pareto frontier, scalarization, weighted-sum cost.
**You should be able to explain**: the tradeoff scalarization makes (one clear
answer) vs. true Pareto search (a set of options, no single decision).

## Step 11 — the three elimination files (any order): `algorithms/GENETIC_ALGORITHM.md`,
`algorithms/ANT_COLONY_OPTIMIZATION.md`, `algorithms/REINFORCEMENT_LEARNING_ROUTING.md`
**Before continuing you should understand**: why these are poor fits *for
pathfinding specifically*, and (for RL) where it might still fit elsewhere.
**Keywords**: population, fitness, pheromone, policy, reward, exploration.
**You should be able to explain**: the one shared reason all three are excluded
from the pathfinding role (exact methods already solve it, optimally and fast,
with better explainability).

## Step 12 — `10_GOAT_DECISION_ENGINE.md`
**Before continuing you should understand**: why perception/prediction/fusion/
routing/signals should stay separate modules, not one fused algorithm.
**Keywords**: separation of concerns, scalarized cost, fusion vs. cost model.
**You should be able to explain**: the difference between what fusion answers and
what the pathfinding algorithm answers.

## Step 13 — `20_ROUTING_ALGORITHM_COMPARISON.md`
**Before continuing you should understand**: how all the candidates stack up
side by side.
**Keywords**: (review of all prior keywords).
**You should be able to explain**: for any row in the table, whether it solves the
same problem as Dijkstra or a genuinely different one.

## Step 14 — `21_ROUTING_EVALUATION_CRITERIA.md`
**Before continuing you should understand**: the 16 criteria and why each is
grounded in a real RESQ design decision, not abstract routing theory.
**Keywords**: determinism, explainability, robustness, route stability.
**You should be able to explain**: three criteria, each tied to a specific file/line
in RESQ's codebase.

## Step 15 — `30_ROUTING_ALGORITHM_RECOMMENDATION.md`
**Before continuing you should understand**: the final recommendation and the
justification for every rejected alternative.
**Keywords**: (synthesis of all prior).
**You should be able to explain**: all 15 of the brief's required questions, from
memory.

## Step 16 — `31_FINAL_ROUTING_ARCHITECTURE.md`
**Before continuing you should understand**: the full pipeline diagram and what
crosses each arrow.
**Keywords**: PERCEPTION, PREDICTION, FUSION, DYNAMIC EDGE-COST CALCULATION,
ROUTING ALGORITHM, SIGNAL PREEMPTION.
**You should be able to explain**: the diagram from memory, and which existing
RESQ file implements each box.

## Step 17 — `40_ROUTING_IMPLEMENTATION_PLAN.md`
**Before continuing you should understand**: the five-stage rollout and its
dependencies.
**Keywords**: staged rollout, differential testing, route_regret_s.
**You should be able to explain**: what would break if Stage 4 were attempted
before Stage 1.

## Step 18 — `60_ROUTING_VIVA_QUESTIONS.md`
**Final check**: attempt every question *before* reading the provided answer; use
mismatches to identify which earlier file to re-read.

## What you should understand before you're "done"

You should be able to explain, unaided, to a non-technical person: what problem
RESQ's routing system solves, why an exact classical algorithm (not AI) does the
actual pathfinding, and where AI/ML legitimately does fit in the system.
