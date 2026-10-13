# Time-Dependent Shortest Path Routing

**Status in RESQ: Implemented (partially).** `routing/engine.py::RouteGraph.shortest_path`
is built this way already; the live demo (`sim/demo.py`) does not yet call it with a
genuinely time-varying cost function.

## 1. One-sentence definition

Time-dependent routing is shortest-path search where a road's cost depends on
*what time you'd actually arrive at it*, not a single fixed number.

## 2. What problem does it solve?

Ordinary Dijkstra/A* assume every edge has one fixed weight for the whole search.
But real traffic isn't fixed — a road might be fast at 2am and slow at 6pm rush
hour, and (more relevant to RESQ's per-trip timescale) a road might be reported as
congested *right now* but the ambulance won't reach it for another four minutes, by
which time conditions may have changed. Time-dependent routing accounts for this by
making edge cost a **function of arrival time**, not a constant.

## 3. Terms I need to know

- **FIFO property (first-in-first-out)**: a time-dependent cost function is
  well-behaved ("FIFO") if leaving an edge *later* never lets you arrive earlier —
  i.e., you can't benefit from delaying departure. Real traffic obeys this in
  practice (waiting doesn't make you overtake yourself), and it's important because
  Dijkstra's core correctness argument depends on costs not behaving in a way that
  rewards waiting.
- **Arrival-time function**: for an edge, a function `travel_time(t)` that says "if
  you enter this edge at time `t`, it takes this long to cross."
- **Departure time / elapsed time**: RESQ's `RouteGraph.shortest_path` tracks
  `departure_time_s` (when the whole trip started) and `elapsed` (how much time has
  passed so far in this specific search branch); the actual clock time you'd enter
  an edge is `departure_time_s + elapsed`.

## 4. Basic idea

Run Dijkstra (or A*) exactly as usual, but every time you'd relax an edge, instead
of adding a *fixed* edge weight, you call a cost function with the *current elapsed
time* to get the weight that applies *right now* for that edge. Because costs can
differ depending on when the search reaches an edge, the algorithm naturally
accounts for "this road will have gotten worse/better by the time I actually get
there."

## 5. Tiny example

Suppose edge A→C costs 2 seconds normally, but a congestion model predicts it will
jump to 20 seconds starting at t=5 (a predicted future traffic buildup), and stay
there. Suppose edge A→B costs a flat 6 seconds always, and B→D a flat 3, and C→D a
flat 1.

- If the ambulance departs at t=0: it reaches C at t=2 (before the buildup at t=5),
  so A→C→D still costs 2+1=3 — cheaper than A→B→D's 9.
- If the ambulance instead had to depart at t=10 (later start): A→C now costs 20
  (buildup already happened), so A→C→D costs 20+1=21 — now A→B→D's 9 is cheaper.

The **same graph, same "fixed" costs on B/D edges**, produces a **different optimal
route depending on departure time** — this is the entire point of time-dependent
routing, and it's a real capability plain (time-independent) Dijkstra cannot
express at all.

## 6. How the algorithm chooses a route

Structurally identical search loop to Dijkstra — the only change is *what number*
gets used for an edge weight at the moment it's relaxed: `cost_fn(edge,
departure_time_s + elapsed)` instead of a constant. This is precisely
`routing/engine.py` line 56: `travel_time = cost_fn(edge, departure_time_s +
elapsed)`.

## 7. What happens if traffic changes?

This is the exact mechanism for representing traffic change over the course of a
single trip — the cost function *is* how "traffic changes" gets expressed to the
search. If traffic changes *after* the search has already committed to a route
(new information arrives mid-trip), that's a **replanning** problem, which is
D* Lite's job (`D_STAR_LITE.md`), not this file's. Time-dependent routing answers
"what's the best route *given predicted future conditions* computed right now";
D* Lite answers "how do I cheaply update that route when *actual* new information
arrives."

## 8. What happens if a road becomes blocked?

A blockage can be modeled as the time-dependent cost function returning a very
large number (or the edge simply being excluded) starting at the time the blockage
is expected/known to exist — this composes naturally with the same mechanism used
for congestion. RESQ's fusion override (travel time forced to 3600s on high-confidence
blockage, `fusion/engine.py` line 47-55) is exactly this idea, just not yet phrased
as a time-varying function.

## 9. What inputs would RESQ provide?

- The graph, as usual.
- A cost function per edge, ideally built from: the current fused estimate
  (`RoadEstimate.travel_time_s`) for "now," plus a forecast (`SpeedForecast` from
  `forecast/models.py`) for "N minutes from now," blended based on how far in the
  future the search's `elapsed` time places the arrival at that edge.

## 10. What output would RESQ receive?

Same `PathResult` shape as plain Dijkstra — a route and a total travel time — but
one that correctly accounts for predicted conditions at the actual time each edge
would be used, not just conditions right now.

## 11. Advantages

- Much more realistic for any trip long enough that conditions could meaningfully
  change en route.
- Directly supports "route around predicted future congestion," not just
  currently-observed congestion — closing the gap identified in
  `01_ROUTING_PROBLEM.md`'s comparison table ("predicted congestion" row).
- No new algorithm family needed — it's a natural extension of Dijkstra/A*, so it
  keeps all their exactness/explainability/performance properties (assuming the
  FIFO property holds).

## 12. Disadvantages

- Requires a genuinely good time-varying cost function — garbage in, garbage out;
  the algorithm is only as good as the forecast feeding it.
- If the FIFO property is violated (a pathological cost function where waiting
  helps), Dijkstra-style search can give wrong answers — must be designed against.
- Slightly more complex cost-function plumbing than a static-weight graph.
- Combining with bidirectional search is non-trivial (see `BIDIRECTIONAL_SEARCH.md`
  §7) since the backward search doesn't know the true arrival time at each edge
  until timing is resolved from the forward direction.

## 13. Computational complexity

Same as the base algorithm used (Dijkstra: O((V+E) log V); A*: same bound, fewer
nodes touched in practice) — time-dependence changes *what* gets computed per edge
relaxation (a function call instead of a constant lookup), not the overall
algorithmic complexity class.

## 14. Real-time suitability

Excellent — this is a lightweight modification, not a heavier algorithm. RESQ
already pays this cost today in `routing/engine.py` with no measurable overhead at
its current scale.

## 15. Emergency ambulance suitability

Very high — this is arguably the single most value-adding technique for RESQ's
stated mission, because it's the mechanism that actually connects *forecasting* to
*routing*. Without it, a forecast model is just a number nobody uses for path
choice.

## 16. Explainability

High, same as Dijkstra/A*, with one addition: you can explain *why* a route was
chosen by also stating *what time* the edge was predicted to be used and *what
cost* applied at that time — a richer, still fully transparent explanation.

## 17. Implementation difficulty

Low — RESQ has already done it (`routing/engine.py`). The remaining work is
*connecting* the cost function to real fused/forecast data, not inventing new
search logic.

## 18. How it fits the existing RESQ architecture

This is not a hypothetical addition — it is the existing `RouteGraph.shortest_path`
design. The gap is that `sim/demo.py`'s live demo does not currently call it with a
genuinely time-varying `edge_cost` function (it uses two precomputed fixed routes
instead, see `00_PROJECT_UNDERSTANDING.md` §4). Wiring `fuse_observations` +
`SpatialTemporalForecaster` output into an `EdgeCost` callable passed to
`RouteGraph.shortest_path` is the natural next step.

## 19. Where it would sit in the pipeline

Spans two components: the **COST MODEL** (deciding what number an edge costs at a
given time, informed by fusion+forecast) and the **PATHFINDING** algorithm itself
(which just needs *some* `EdgeCost` callable — it doesn't care where the numbers
came from). This is the clean separation `10_GOAT_DECISION_ENGINE.md` argues for.

## 20. Simple pseudocode

```text
function time_dependent_dijkstra(graph, source, goal, departure_time, cost_fn):
    # cost_fn(edge, clock_time) -> travel_time_seconds
    dist = { source: 0 }
    prev = {}
    queue = min-heap containing (0, source)

    while queue not empty:
        (elapsed, node) = queue.pop_min()
        if elapsed > dist.get(node, infinity):
            continue
        if node == goal:
            return reconstruct_path(prev, goal), elapsed
        for edge in graph.edges_from(node):
            clock_time = departure_time + elapsed
            travel_time = cost_fn(edge, clock_time)     # <-- the only change vs. Dijkstra
            candidate = elapsed + travel_time
            if candidate < dist.get(edge.target, infinity):
                dist[edge.target] = candidate
                prev[edge.target] = (node, edge)
                queue.push((candidate, edge.target))

    return "no path found"
```

This is line-for-line `RouteGraph.shortest_path` in `routing/engine.py`.

## 21. What I should remember for viva

- Time-dependent routing is Dijkstra/A* with `edge_cost(edge, time)` instead of a
  constant — same algorithm family, richer input.
- The correctness of this depends on the **FIFO property** (no benefit to
  waiting) — worth naming if asked "does this still give the optimal answer?"
- RESQ already implements the mechanism (`routing/engine.py`); the gap is
  *connecting it to live fused/forecast data* in the demo, not building it from
  scratch.
- This is the technique that actually makes "predicted congestion" (from
  `01_ROUTING_PROBLEM.md`'s requirements table) usable by the router.

## 22. One practical example

An ambulance departing now might see road X reported as clear by macro data, but a
15-minute forecast (`SpatialTemporalForecaster.predict(..., horizons=(15,))`)
predicts it will be congested by the time a *later* part of the route reaches it.
A time-dependent router, unlike a static one, could route around that predicted
future congestion on the first computation — no replanning needed, because the
prediction was already accounted for.

## What you should understand before reading the next file

You should be able to explain the FIFO property in your own words and why it
matters for correctness. You should also be able to point to the exact line in
`routing/engine.py` where time-dependence enters the algorithm. Next:
`MULTI_OBJECTIVE_ROUTING.md`, which addresses combining *several kinds* of cost
(not just time) into one decision.
