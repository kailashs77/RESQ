# Bidirectional Search (Bidirectional Dijkstra / Bidirectional A*)

**Status in RESQ: Candidate algorithm — not currently implemented.**

## 1. One-sentence definition

Bidirectional search runs two shortest-path searches at once — one forward from
the start, one backward from the goal — and stops as soon as they meet in the
middle.

## 2. What problem does it solve?

The exact same single-source, single-destination shortest path problem as Dijkstra
and A*, solved faster when both endpoints (source and destination) are known in
advance — which is RESQ's situation: the ambulance's current position and the
hospital are both known before the search starts.

## 3. Terms I need to know

- **Forward search**: a normal Dijkstra/A* search starting at the source, following
  edges in their normal direction.
- **Backward search**: a Dijkstra/A* search starting at the destination, following
  edges in *reverse* (if a real edge goes X→Y, the backward search conceptually
  uses Y→X) to find how to reach the destination from anywhere.
- **Meeting point**: a node reached by both searches; once found, the best combined
  path through any meeting point is a candidate for the shortest path.

## 4. Basic idea

1. Run a forward search from the source and a backward search from the destination,
   alternating steps between them (e.g., one step forward, one step backward,
   repeat).
2. Track the best path length seen so far through any node visited by both
   searches.
3. Stop when the sum of the smallest unprocessed frontier costs in both directions
   exceeds the best known combined path — at that point no better meeting point can
   exist.
4. Reconstruct the path by joining the forward path to the meeting node with the
   backward path from the meeting node to the destination (reversed).

The intuition for the speedup: each search only needs to expand roughly "half the
distance" to the middle instead of the whole distance to the far side, and since
graph search cost grows roughly with the *area* covered (in 2D road networks), two
half-radius searches together typically cover meaningfully less area than one
full-radius search.

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

Forward search from A: visits A (0), C (2), B (6)...
Backward search from D (reverse edges: D→C, B→D reversed to D→B, C→D reversed
already covered): visits D (0), C (1, via reversed D→C edge, cost 1)...

Forward and backward searches meet at **C**: forward cost to C = 2, backward cost
to C = 1 (the reverse of C→D). Combined path cost via C = 2 + 1 = 3, matching the
true shortest path A→C→D found earlier — but on this tiny graph both searches
essentially finish in one or two steps each, so the benefit is invisible; the real
payoff shows up on graphs with thousands of nodes where "meet in the middle"
genuinely means visiting far fewer nodes than either single-direction search would.

## 6. How the algorithm chooses a route

Same underlying correctness rule as Dijkstra/A* (expand cheapest frontier node
next), applied independently in each direction, with an extra termination check
comparing the best-known meeting path against the two frontiers' lower bounds.

## 7. What happens if traffic changes?

Works the same way as single-direction Dijkstra: rerun with updated costs. There is
one real subtlety with **time-dependent** costs (see `TIME_DEPENDENT_ROUTING.md`):
the backward search doesn't know what time the ambulance will actually be at each
edge until the forward search has (partly) resolved timing, since travel time
depends on *when* you enter an edge. This makes naive bidirectional search harder
to combine correctly with time-dependent edge costs than with fixed costs — it is
solvable, but adds implementation complexity RESQ would need to handle carefully
(commonly via an approximate backward heuristic rather than an exact backward
search).

## 8. What happens if a road becomes blocked?

Same as Dijkstra: exclude blocked edges from both the forward and backward
adjacency exploration, then rerun. Blockage handling is identical in spirit;
bidirectional search doesn't add or remove any capability here, only speed.

## 9. What inputs would RESQ provide?

The same graph, source, and destination as Dijkstra/A* — plus a **reverse
adjacency list** (which edges point *into* each node), which RESQ's current
`adjacency: dict[str, list[RoadEdge]]` (outgoing edges only) does not yet build,
so this would be a genuinely new small data structure, not just a search-order
change like A* was.

## 10. What output would RESQ receive?

Identical output shape and (if implemented correctly) identical optimal cost to
Dijkstra/A* — again, this is purely a performance technique.

## 11. Advantages

- Can meaningfully reduce the number of nodes explored versus one-directional
  search, especially as the graph grows.
- Still exact/deterministic when implemented correctly.
- Composes with A* (bidirectional A*) for further speedup.

## 12. Disadvantages

- More complex to implement correctly than plain Dijkstra/A* — the termination
  condition and path reconstruction are easy to get subtly wrong.
- Needs a reverse adjacency structure (extra memory, extra one-time build cost).
- Combining cleanly with time-dependent edge costs is genuinely harder (see #7) —
  this is a real engineering cost, not just a footnote.
- At RESQ's *current* small graph size, the benefit is negligible; this is a
  "useful later, not urgent now" technique.

## 13. Computational complexity

Same worst-case Big-O as Dijkstra/A*, **O((V+E) log V)**, but empirically often
much faster in practice on large graphs (rough intuition: two √(area) searches
instead of one full-area search). Space roughly doubles versus one-directional
search (two frontiers, two visited sets, plus the reverse adjacency list).

## 14. Real-time suitability

Good, and more valuable as the graph grows. Not a priority at RESQ's current tiny
demo scale; worth having in the toolbox if RESQ's map coverage expands
significantly.

## 15. Emergency ambulance suitability

Same correctness/explainability profile as Dijkstra/A* (it's still an exact
method) — fine for an ambulance system, contingent on getting the time-dependent
interaction (#7) right if used together with live traffic costs.

## 16. Explainability

Same as Dijkstra/A*: final answer is a transparent sum of edge costs. The
bidirectional *search process* is slightly less intuitive to explain to a
non-technical reviewer than "explore outward from start," but the *result* is
exactly as explainable.

## 17. Implementation difficulty

Moderate — noticeably higher than Dijkstra or A* alone, due to the termination
condition, the need for a reverse graph, and (if combined with time-dependent
costs) the timing-coordination subtlety in #7.

## 18. How it fits the existing RESQ architecture

Would replace/augment `RouteGraph.shortest_path`'s internals while keeping the same
public signature (`start`, `goal`, `edge_cost` → `PathResult`). Requires building
and maintaining a reverse adjacency alongside the existing forward one.

## 19. Where it would sit in the pipeline

PATHFINDING component — a performance variant of the same stage Dijkstra/A*
occupy, not a new pipeline stage.

## 20. Simple pseudocode

```text
function bidirectional_dijkstra(graph, reverse_graph, source, goal):
    forward = Dijkstra-state starting at source
    backward = Dijkstra-state starting at goal, using reverse_graph
    best = infinity
    meeting_node = none

    while both frontiers non-empty and
          forward.min_frontier_cost + backward.min_frontier_cost < best:
        step forward search by one node; update best/meeting_node if it
            touches a node backward has already finalized
        step backward search by one node; same check

    return reconstruct(meeting_node, forward, backward), best
```

## 21. What I should remember for viva

- Same optimality guarantee as Dijkstra; purely a speed optimization.
- Needs a reverse graph — the one real new piece of infrastructure.
- Combining with time-dependent (live-traffic) costs is genuinely trickier than
  the fixed-cost case — worth naming explicitly if asked "why not do this first?"
- Not yet implemented in RESQ; classified **B — possible candidate**, secondary to
  the core Dijkstra/A*/D* Lite recommendation.

## 22. One practical example

If RESQ's map grew from ~a few hundred nodes to full Chennai scale (hundreds of
thousands of nodes), bidirectional A* between the ambulance's current position and
a fixed hospital would be a natural next optimization on top of the already-chosen
A* — a "when we need more speed" tool, not a "must-have on day one" tool.

## What you should understand before reading the next file

You should be able to explain, in your own words, why knowing *both* endpoints in
advance (as RESQ always does — ambulance position, fixed hospital) is exactly what
makes bidirectional search applicable, whereas it wouldn't help for "find the
nearest hospital of any kind" (unknown destination). Next: `D_STAR_LITE.md`, which
addresses a different problem — efficient *replanning*, not raw search speed.
