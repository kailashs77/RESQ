# Dijkstra's Algorithm

## 1. One-sentence definition

Dijkstra's algorithm finds the cheapest way to get from a starting point to every
other point in a graph, by always exploring the currently-cheapest-known unexplored
point next.

## 2. What problem does it solve?

The **single-source shortest path problem**: given a starting node, find the
minimum-cost path from it to one (or every) other node, when all edge costs are
zero or positive (never negative).

## 3. Terms I need to know

- **Frontier**: the set of nodes we know how to reach but haven't yet "locked in"
  the cheapest way to reach.
- **Priority queue (min-heap)**: a data structure that always hands you the
  smallest item first, efficiently. RESQ uses Python's `heapq` for this.
- **Relaxation**: the act of checking "is there a cheaper way to reach this node
  through the node I'm currently processing?" and updating if so.
- (See `03_GRAPH_THEORY_FOR_RESQ.md` for node/edge/weight/path if needed.)

## 4. Basic idea

1. Start at the source. Its cost-to-reach is 0; every other node's is "unknown/infinity."
2. Repeatedly: take the *unprocessed* node with the smallest known cost-to-reach
   (using the priority queue).
3. For each edge leaving that node, check: is `(cost to reach this node) + (edge
   weight)` cheaper than the best known cost to reach the edge's target? If yes,
   update it ("relax" the edge), and remember which edge got us there (for path
   reconstruction).
4. Mark the node processed. Repeat until the destination is processed (or the queue
   is empty).
5. Reconstruct the path by walking backward through the "remembered edges."

The key guarantee: once a node is popped from the priority queue with a certain
cost, that cost is *final and optimal* — this only holds because weights are
non-negative (a cheaper route could never sneak in later through a negative edge).

## 5. Tiny example

```text
        6
   A ------> B
   |         |
  2|         |3
   v         v
   C ------> D
        1
```

Start at A. Frontier: `{A:0}`.
- Pop A (cost 0). Relax: B ← 6, C ← 2.
- Pop C (cost 2, cheapest). Relax: D ← 2+1=3.
- Pop D (cost 3, cheapest of remaining {B:6, D:3}). D reached optimally at cost 3.
- (If B were the destination, we'd pop it next at cost 6.)

Shortest path A→D = **A→C→D, cost 3** (cheaper than A→B→D's cost 9).

## 6. How the algorithm chooses a route

It never "guesses" — it provably always expands the cheapest frontier node next,
so by the time a node is popped, no cheaper path to it can still be found (because
any such path would have to go through an even-cheaper unpopped node, which
contradicts "we always pop the cheapest"). This is why Dijkstra is *exact*: it does
not approximate.

## 7. What happens if traffic changes?

Plain Dijkstra assumes fixed edge weights for the whole search. If traffic changes
*while the algorithm computes* nothing happens mid-search (it's typically fast
enough this doesn't matter at RESQ's graph size). If traffic changes *between*
route computations, you simply rerun Dijkstra with updated weights — RESQ's
`routing/engine.py::RouteGraph.shortest_path` goes further: its `edge_cost`
callback can vary the cost **by the time-of-arrival at the edge**, so a single
search run can already account for "this road will be slow when I actually get
there in 4 minutes" — this is the time-dependent extension, detailed in
`TIME_DEPENDENT_ROUTING.md`.

## 8. What happens if a road becomes blocked?

RESQ handles this today by **removing the edge from consideration entirely**:
`RoadNetwork.route(start, finish, blocked=frozenset({edge_id, ...}))` simply skips
any edge whose id is in `blocked` (`sim/chennai/roads.py` lines 116-117), then
reruns the full Dijkstra search. This works, but recomputes the entire search from
scratch. For frequent/large graphs, `D_STAR_LITE.md` describes how to avoid that
recomputation.

## 9. What inputs would RESQ provide?

- The graph (`adjacency`), already built from `sim/chennai/roads.json`.
- A start node (ambulance's nearest road junction) and goal node (hospital's
  nearest road junction) — `RoadNetwork.nearest_node`.
- Edge costs — currently constant-speed travel time; ideally, fused travel-time
  estimates from `fusion/engine.py`.
- Optionally, a blocked-edge set from a confirmed incident.

## 10. What output would RESQ receive?

A `PathResult` / `RoadPath`: the ordered edges forming the route, and the total
travel time — exactly what `RouteOption.edge_ids` and `RouteOption.eta_s` are
populated from today.

## 11. Advantages

- **Exact**: always finds the true minimum-cost path (given non-negative weights).
- **Deterministic**: same input ⇒ same output, always. Easy to test, easy to
  explain ("this is the sum of these edge costs").
- **Well-understood, simple to implement correctly**: RESQ has two independent
  correct implementations already.
- **Naturally extends to time-dependent costs** with a small modification (see #7).

## 12. Disadvantages

- Explores in all directions from the source; can visit many more nodes than
  strictly necessary if you already know roughly where the destination is
  (A* fixes this — see `A_STAR.md`).
- Requires non-negative weights (never an issue for travel time, so not a real
  limitation for RESQ).
- A full rerun from scratch is needed whenever the graph changes, unless you use a
  dynamic-replanning variant (D* Lite).

## 13. Computational complexity

With a binary heap: **O((V + E) log V)**, where V = number of nodes, E = number of
edges. RESQIt's Chennai extract has a small V (a few hundred nodes in the demo
bounding box), so this runs in well under a millisecond in practice.
Space: **O(V + E)** to store the graph, plus O(V) for the distance/priority-queue
bookkeeping.

## 14. Real-time suitability

Excellent, at RESQ's current graph scale. Even city-scale OSM graphs (hundreds of
thousands of nodes) run Dijkstra in tens to low-hundreds of milliseconds; RESQ's
tiny extract is effectively instant.

## 15. Emergency ambulance suitability

High. The exactness and determinism matter *specifically* for a safety-critical
system: you want a provably correct "best known route," not an approximate one,
and you want to be able to explain the choice after the fact (accident reports,
review boards, viva examiners).

## 16. Explainability

Very high. The final route's cost is literally the sum of a small number of edge
costs; you can print each edge and its contribution. This is a real practical
advantage over black-box methods.

## 17. Implementation difficulty

Low. Roughly 30-40 lines of Python with a heap, as RESQ's own two implementations
show (`routing/engine.py`, `sim/chennai/roads.py`).

## 18. How it fits the existing RESQ architecture

It **is** the existing RESQ architecture's pathfinding core — both
`sim/chennai/roads.py::RoadNetwork.route` and `routing/engine.py::RouteGraph.shortest_path`
are Dijkstra. No new algorithm needs to be introduced for the base capability; the
open question is *cost model richness* (what numbers feed the edges) and
*replanning efficiency* (D* Lite), not "which pathfinding algorithm."

## 19. Where it would sit in the pipeline

PATHFINDING component (see `02_ALGORITHM_CANDIDATES.md`'s four-component
breakdown) — it sits downstream of fusion/forecast (which decide edge costs) and
upstream of the signal-preemption layer (which reacts to the chosen route's ETA).

## 20. Simple pseudocode

```text
function dijkstra(graph, source, goal):
    dist = { source: 0 }
    prev = {}
    queue = min-heap containing (0, source)

    while queue is not empty:
        (cost, node) = queue.pop_min()
        if cost > dist.get(node, infinity):
            continue                     # stale queue entry, skip
        if node == goal:
            return reconstruct_path(prev, goal), cost
        for edge in graph.edges_from(node):
            new_cost = cost + edge.weight
            if new_cost < dist.get(edge.target, infinity):
                dist[edge.target] = new_cost
                prev[edge.target] = (node, edge)
                queue.push((new_cost, edge.target))

    return "no path found"
```

This is, almost line for line, `RouteGraph.shortest_path` in `routing/engine.py`.

## 21. What I should remember for viva

- Dijkstra requires non-negative weights; travel time is always non-negative, so
  this is never a real constraint for RESQ.
- It is *exact*, not approximate — this is why it (and not GA/ACO/RL) is the right
  choice for a safety-critical single-route query.
- RESQ has **two** Dijkstra implementations: a simple one baked into the demo road
  graph (`sim/chennai/roads.py`), and a general, reusable, time-dependent-capable
  one (`routing/engine.py`) not yet wired into the live demo.
- Complexity: O((V+E) log V) — trivial at RESQ's current graph size.

## 22. One practical example

When `demo_plan()` runs at startup, it calls `RoadNetwork.route(start, finish)` once
to get the "normal" route (plain Dijkstra, no blocks), then calls
`RoadNetwork.route(start, finish, blocked=<some section>)` again to get the
"bypass" route (Dijkstra again, with edges removed). Both are the exact same
algorithm; the difference is purely which edges are available to explore.

## What you should understand before reading the next file

You should be able to trace Dijkstra by hand on the 4-node example in
`03_GRAPH_THEORY_FOR_RESQ.md` and get the same answer as shown there. You should
also be able to say why Dijkstra explores "wastefully" in all directions — that
motivates `A_STAR.md`.
