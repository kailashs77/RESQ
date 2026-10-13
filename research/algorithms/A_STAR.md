# A* (A-Star) Search

**Current status: Implemented and measured.** `routing/engine.py` provides A*,
`sim/chennai/roads.py` supplies an admissible geometric heuristic derived from
the actual weights, and Brain calls it for full-graph replanning. The evaluation
report records both time and explored nodes. Fewer nodes does not guarantee lower
wall-clock time on every input. Dijkstra verifies equal optimal cost.

## 1. One-sentence definition

A* is Dijkstra's algorithm with a built-in sense of direction: it prefers to
explore nodes that *seem* closer to the goal, using an estimate, while still
guaranteeing the exact optimal answer.

## 2. What problem does it solve?

The same problem as Dijkstra (single-source, single-destination shortest path),
but solves it **faster in practice** when you have a *known destination* and a
reasonable way to estimate remaining distance/cost to it — which is exactly RESQ's
situation (fixed hospital destination, real lat/lon coordinates available).

## 3. Terms I need to know

- **Heuristic function `h(n)`**: an estimate of the cost remaining from node `n` to
  the goal. For road networks, straight-line ("as the crow flies") distance divided
  by a plausible top speed is a natural heuristic.
- **Admissible heuristic**: one that never *overestimates* the true remaining cost.
  This is the property that keeps A* exact/correct.
- **Consistent (monotone) heuristic**: a stronger property where, for every edge
  `(n, m)`, `h(n) ≤ edge_cost(n, m) + h(m)`. Consistent heuristics guarantee A*
  never needs to re-expand a node — straight-line distance is consistent when edge
  costs are travel times bounded below by a real speed limit.
- **`f(n) = g(n) + h(n)`**: the number A* actually sorts its frontier by.
  `g(n)` = known cost so far to reach `n` (same as Dijkstra's running cost).
  `h(n)` = the heuristic estimate of the rest of the trip.

## 4. Basic idea

Identical to Dijkstra, except the priority queue is ordered by `f(n) = g(n) + h(n)`
instead of just `g(n)`. Nodes that are cheap to reach *and* look close to the goal
get explored first. If `h(n) = 0` for every node, A* becomes exactly Dijkstra — so
Dijkstra is a special case of A*.

## 5. Tiny example

Same graph as before, but imagine A, B, C, D have straight-line distances to D
(the goal) of h(A)=5, h(B)=3, h(C)=1, h(D)=0 (made up but admissible — never bigger
than the true remaining road cost).

```text
        6
   A ------> B
   |         |
  2|         |3
   v         v
   C ------> D
        1
```

- Start: f(A) = g(A)+h(A) = 0+5 = 5. Pop A.
- Relax B: g=6, f=6+3=9. Relax C: g=2, f=2+1=3.
- Frontier: {B: f=9, C: f=3}. C has smaller f — pop C first (Dijkstra, sorting only
  by g, would have made the same choice here since C also has smaller g; the
  difference shows up more on larger graphs where the heuristic actively steers
  search away from irrelevant branches).
- Relax D via C: g=3, f=3+0=3. Pop D (goal reached) at cost 3 — same optimal answer
  as Dijkstra, but on a large graph A* would have explored far fewer irrelevant
  nodes far away from the B-side of the graph.

## 6. How the algorithm chooses a route

Same rule as Dijkstra — always expand the frontier node with smallest `f` — but the
`h` term biases exploration toward the goal directionally, so fewer "wrong
direction" nodes get visited before the destination is reached.

## 7. What happens if traffic changes?

Same situation as Dijkstra: the heuristic is about *geometric* distance to the
goal, not traffic — it doesn't go stale when traffic changes (straight-line
distance to the hospital never changes), only `g(n)` (the accumulated real cost) is
affected by live/predicted travel times. A* is just as compatible with
time-dependent edge costs as Dijkstra is, *as long as the heuristic still never
overestimates the true remaining cost even under the worst-case (fastest possible)
travel time* — see `TIME_DEPENDENT_ROUTING.md` for the subtlety this introduces.

## 8. What happens if a road becomes blocked?

Identical handling to Dijkstra: remove blocked edges from consideration, rerun
search. A* doesn't change this story; it only changes how quickly the (re)search
finds the answer.

## 9. What inputs would RESQ provide?

Everything Dijkstra needs, **plus** a heuristic function. For RESQ, the natural
choice: haversine straight-line distance from a node to the hospital's coordinates,
divided by the fastest plausible road speed in the network (to guarantee it never
overestimates true travel time). RESQ already has a haversine function
(`sim/chennai/roads.py::distance_m`) ready to reuse for exactly this.

## 10. What output would RESQ receive?

Identical output shape to Dijkstra — a `PathResult`/`RoadPath` with the same
optimal cost. A* never gives a *different* (worse) answer than Dijkstra when the
heuristic is admissible; it only gets there faster.

## 11. Advantages

- Same optimality guarantee as Dijkstra (given an admissible heuristic).
- Typically explores far fewer nodes on large graphs — real time savings.
- Still fully deterministic and explainable.

## 12. Disadvantages

- Needs a good heuristic; a poor or inadmissible one can either give no speed
  benefit or (if inadmissible) break the optimality guarantee.
- Slightly more implementation complexity than plain Dijkstra (one more function,
  one more term in the priority comparison).
- On RESQ's *current* tiny graph, the speed benefit is negligible — the value is
  more about correctness of habit and scaling to a bigger real map later.

## 13. Computational complexity

Same worst-case bound as Dijkstra, **O((V+E) log V)**, but the *practical* number
of nodes actually expanded is typically much smaller with a good heuristic — this
doesn't change the Big-O bound but matters a lot in real wall-clock time on large
graphs. Space: O(V+E), same as Dijkstra.

## 14. Real-time suitability

Excellent — same as Dijkstra at RESQ's scale, better than Dijkstra at true city
scale (tens of thousands+ of nodes), which is the size RESQ would need if it
expanded beyond the current small demo bounding box.

## 15. Emergency ambulance suitability

High, for the same determinism/explainability reasons as Dijkstra, with the added
benefit that if RESQ's map ever grows to real city scale, A* keeps response time
low without sacrificing correctness — important when a route needs to be
(re)computed under time pressure.

## 16. Explainability

Same as Dijkstra: the final path's cost is a transparent sum of edge costs. The
heuristic used to *search* the graph faster does not appear in the final answer's
cost at all — it's purely a search-order optimization, not a decision factor.

## 17. Implementation difficulty

Low-to-moderate. It is Dijkstra plus one extra term in the priority comparison and
one small function (the heuristic). RESQ's codebase already has all the pieces
needed (coordinates, haversine distance) to add this without new dependencies.

## 18. How it fits the existing RESQ architecture

A* would be a **drop-in enhancement** to `routing/engine.py::RouteGraph.shortest_path`
or `sim/chennai/roads.py::RoadNetwork.route` — same function signature, same
output type, only the priority-queue ordering changes. It requires no change to
`fusion/`, `schema/`, or the API layer.

## 19. Where it would sit in the pipeline

PATHFINDING component — same position as Dijkstra, since A* *is* Dijkstra with a
heuristic. It is not a separate stage of the pipeline.

## 20. Simple pseudocode

```text
function a_star(graph, source, goal, h):
    g = { source: 0 }
    prev = {}
    queue = min-heap containing (h(source), source)

    while queue is not empty:
        (f, node) = queue.pop_min()
        if node == goal:
            return reconstruct_path(prev, goal), g[node]
        for edge in graph.edges_from(node):
            new_g = g[node] + edge.weight
            if new_g < g.get(edge.target, infinity):
                g[edge.target] = new_g
                prev[edge.target] = (node, edge)
                queue.push((new_g + h(edge.target), edge.target))

    return "no path found"
```

## 21. What I should remember for viva

- A* = Dijkstra + a goal-directed heuristic; with `h(n)=0` everywhere, it reduces
  exactly to Dijkstra.
- The heuristic must be **admissible** (never overestimate) to guarantee the
  correct optimal answer is still found.
- Straight-line distance / fastest-plausible-speed is a standard, provably
  admissible heuristic for road networks.
- Same output, same guarantees as Dijkstra — the only difference is search speed.

## 22. One practical example

If RESQ's road extract were expanded to cover all of Chennai (tens of thousands of
nodes) instead of one small bounding box, switching `RoadNetwork.route` from plain
Dijkstra to A* with a haversine-based heuristic toward the hospital would keep
route computation fast without changing a single line of the fusion, schema, or
API code — because the function's inputs and outputs are unchanged.

## What you should understand before reading the next file

You should be able to explain what "admissible" means and why a heuristic that
overestimates could cause A* to miss the true shortest path. Next, read
`BIDIRECTIONAL_SEARCH.md` for another practical speed technique that composes with
both Dijkstra and A*.
