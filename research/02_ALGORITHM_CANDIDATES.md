# 02 — Algorithm Candidates: Broad Survey and Filtering

Before studying anything deeply, RESQ needs a wide net cast, then narrowed. This
file classifies every algorithm the brief asked about into:

- **A — Strong candidate**: gets a full detailed file in `research/algorithms/`.
- **B — Possible candidate**: gets a full detailed file, but is expected to be a
  secondary/optional piece, not the core recommendation.
- **C — Poor fit**: explained here, eliminated, *no* detailed file (per the filtering
  rule) — but because the brief explicitly asked for a beginner teaching file on
  Genetic Algorithm, Ant Colony Optimization, and Reinforcement Learning, those three
  get a **short elimination-focused file** instead of a full 22-section deep dive.
  That is noted explicitly in each of those files.
- **D — Irrelevant**: explained briefly, no file at all.

Also — critical distinction the brief insists on — every algorithm below is labeled
with **which of the four RESQ problem components it belongs to**, because they are
not interchangeable:

1. **PATHFINDING** — finds the actual route through the graph, given edge costs.
2. **COST/WEIGHT MODEL** — decides what an edge costs (traffic, prediction, risk...).
3. **FUSION/DECISION LAYER** — combines multiple data sources into an estimate.
4. **SIGNAL PREEMPTION** — coordinates traffic lights for the emergency vehicle.

Most of the algorithms below belong to component (1), PATHFINDING. Genetic
Algorithm / Ant Colony Optimization / Reinforcement Learning are sometimes proposed
for (1) too, but as shown below, they are a poor fit for RESQ's actual shape of
problem (single vehicle, single query, hard real-time, needs correctness guarantees).

---

## Candidate table

| # | Algorithm | Component | Classification | One-line reason |
|---|---|---|---|---|
| 1 | Dijkstra | Pathfinding | **A** | Already implemented twice in RESQ; the correct baseline for non-negative, single-source shortest paths. |
| 2 | A* | Pathfinding | **A** | Same guarantees as Dijkstra but faster with a good heuristic (straight-line distance is a natural, admissible heuristic for road graphs). Directly applicable to RESQ's graph. |
| 3 | Bidirectional Dijkstra | Pathfinding | **B** | Real speed win on a single fixed origin→destination query; RESQ's origin/hospital pair is fixed, so this is relevant but is an optimization, not a new capability. |
| 4 | Bidirectional A* | Pathfinding | **B** | Combines #2 and #3; same relevance/caveats. Care needed for correctness with time-dependent costs (see file). |
| 5 | Bellman-Ford | Pathfinding | **D** | Solves a strictly harder problem (handles negative edge weights) that RESQ does not have — travel time is never negative. Slower than Dijkstra for no benefit here. |
| 6 | Floyd-Warshall | Pathfinding | **D** | Computes all-pairs shortest paths. RESQ only ever needs one origin (ambulance) to one destination (hospital) at a time — wasteful by orders of magnitude, and does not naturally support time-dependent costs. |
| 7 | Yen's K-shortest paths | Pathfinding | **B** | Genuinely useful: gives the driver/dispatcher a small set of ranked alternative routes (not just one), which fits RESQ's "route options" UI concept (`RouteOption` list). Not currently implemented, but directly implementable on top of the existing Dijkstra core. |
| 8 | D* / D* Lite | Pathfinding (dynamic replanning) | **A** | Purpose-built for "replan efficiently when the graph changes" (a road becomes blocked, a cost updates) without rerunning search from scratch. This is *exactly* RESQ's blockage/incident scenario. |
| 9 | Lifelong Planning A* (LPA*) | Pathfinding (dynamic replanning) | **B** | The algorithm D* Lite is built on top of; worth understanding as the stepping stone, but D* Lite (its extension for a moving start) is the more directly relevant one for RESQ. |
| 10 | Time-dependent shortest path algorithms | Cost model + Pathfinding | **A** | Not a single algorithm but a *modification* (time-dependent edge costs, e.g. Dijkstra where cost = f(edge, arrival_time)). RESQ's `routing/engine.py` is already built this way. Central to the recommendation. |
| 11 | Multi-objective shortest path approaches | Cost model + Pathfinding | **A** | RESQ's real need (time + safety + confidence + blockage risk) is multi-criteria. Understanding how to responsibly combine into ONE scalar cost (vs. true Pareto search) is central to `10_GOAT_DECISION_ENGINE.md`. |
| 12 | Weighted-cost routing | Cost model | **A** (folded into #11's file) | This is the practical technique RESQ should use: a weighted-sum cost function, not full Pareto search. Covered inside `MULTI_OBJECTIVE_ROUTING.md` rather than as a separate file, since it is the same idea applied simply. |
| 13 | Constrained shortest path | Pathfinding + constraints | **B** (folded into Dijkstra/A* files) | RESQ already does this trivially (removing blocked edges = a hard constraint). Worth naming explicitly; does not need its own file, since it's a small modification to #1/#2, not a separate algorithm family. |
| 14 | Dynamic shortest path approaches (general) | Pathfinding | **A** (folded into D* Lite + time-dependent files) | Overlaps directly with #8 and #10; not a separate file to avoid duplication. |
| 15 | Genetic Algorithm | Pathfinding (metaheuristic) | **C** | Poor fit: stochastic, no optimality guarantee, needs many generations/evaluations — wrong shape for a single real-time query where Dijkstra/A* already solve it exactly and fast. Gets a short elimination file because the brief asked for one. |
| 16 | Ant Colony Optimization | Pathfinding (metaheuristic) | **C** | Poor fit for the same core reason as GA: designed for repeatedly-solved, large, complex combinatorial problems (e.g., vehicle routing with many stops) where near-optimal heuristics beat exact methods on cost. RESQ's problem (one vehicle, one graph, need exact/fast/explainable) doesn't match. Short elimination file included. |
| 17 | Reinforcement Learning routing | Pathfinding / Policy learning | **C** | Poor fit *for the pathfinding step itself*: RL needs extensive training data/simulation, is non-deterministic and hard to certify safe, and Dijkstra/A* already solve shortest-path exactly given a cost. RL is a legitimate idea *elsewhere* (e.g., learning signal timing policy, or learning to predict congestion) — see the file for where it could fit outside pathfinding. Short elimination file included. |
| 18 | Multi-agent / multi-objective optimization (general) | System-level | **D** for pathfinding purposes | RESQ, today, models exactly one ambulance. Multi-agent coordination (multiple ambulances competing for the same green corridor) is a real future problem but is out of scope for "which pathfinding algorithm" — it is a scheduling/coordination problem layered on top of, not a replacement for, the chosen pathfinding algorithm. |

## Why the C/D algorithms are eliminated early (short version, full detail in their files)

**Genetic Algorithms, Ant Colony Optimization, and Reinforcement Learning** all
share three problems for RESQ's specific job (find the best route from a fixed
ambulance position to a fixed hospital, right now, with a graph of maybe a few
hundred to a few thousand nodes):

1. **They solve approximately**, with no guarantee of finding the true shortest
   path — Dijkstra/A* find it *exactly*, and exactly is achievable here because the
   graph is small enough that exact methods are already fast (milliseconds).
2. **They are non-deterministic or need extensive tuning/training**, which conflicts
   with RESQ's explicit design philosophy (see `corridor/state_machine.py` docstring:
   *"ML never owns a signal transition"*, and the project's repeated emphasis on
   explainability in `docs/REVIEW_II_VIVA_NOTES.md`). A professor or safety reviewer
   cannot easily verify "why did the ambulance pick this road?" from a GA population
   or an RL policy's weights the way they can from a Dijkstra cost breakdown.
3. **They are built for problems RESQ doesn't have**: GA/ACO shine on large
   combinatorial problems with many interacting constraints solved *repeatedly*
   offline (e.g., delivery-fleet routing with hundreds of stops); RL shines on
   *sequential decision problems where the right action depends on a policy learned
   from experience* (e.g., a control policy, not a one-shot pathfinding query).
   RESQ's routing query is a single-shot, well-structured shortest-path problem —
   exact classical algorithms are the correct tool, full stop.

This does **not** mean "AI has no role in RESQ." It means AI's proper role is
**upstream of pathfinding** — in the COST MODEL (predicting future congestion) and
in the FUSION layer (combining sensor sources) — not as the pathfinding algorithm
itself. This distinction is the single most important idea in this whole research
set; see `10_GOAT_DECISION_ENGINE.md`.

## What you should understand before reading the next file

You should be able to say, out loud, in one sentence each: why Bellman-Ford and
Floyd-Warshall don't fit RESQ; why D* Lite is specifically relevant to the incident
scenario; and why GA/ACO/RL are not being used *as the pathfinding algorithm* even
though they're valid, well-known ideas in general routing research. Next:
`03_GRAPH_THEORY_FOR_RESQ.md`.
