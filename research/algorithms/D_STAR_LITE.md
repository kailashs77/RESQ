# D* Lite (and its ancestor, Lifelong Planning A* / LPA*)

**Status in RESQ: Candidate algorithm — not currently implemented.** RESQ today
handles blockages by *rerunning Dijkstra from scratch* (`sim/chennai/roads.py::demo_plan`,
via `RoadNetwork.route(..., blocked=...)`). D* Lite is the algorithm family that
avoids that full rerun.

Priority 1 adds fresh **A*** searches in the Brain mission. This is still a full
search, with no incremental D* Lite state. D* Lite remains future work and has
no measured comparison row.

## 1. One-sentence definition

D* Lite finds a shortest path the same way A* does, but when the graph later
changes (a road closes, a cost updates), it repairs the existing search instead of
starting over.

## 2. What problem does it solve?

**Efficient replanning**: you already computed a shortest path; now the world
changed slightly (one or a few edges got more expensive or blocked); you want an
updated shortest path *fast*, without redoing all the work that's still valid.

## 3. Terms I need to know

- **Incremental search**: a search that reuses previous results and only
  recomputes the parts affected by a change.
- **LPA\* (Lifelong Planning A\*)**: the earlier algorithm D* Lite is built on. It
  efficiently repairs a shortest-path-tree from a *fixed start* when edge costs
  change. D* Lite extends this to also handle a *moving start* (the searching agent
  itself moving through the graph) efficiently — relevant since the ambulance is
  physically moving while the graph might change.
- **Inconsistent node**: internally, D*/LPA* mark nodes whose cost estimate may no
  longer be correct after a change, and only reprocess those (and nodes affected by
  them), not the whole graph.
- **Rhs-value / g-value** (implementation detail, not required for a beginner
  understanding, but appears in textbooks): two cost estimates per node
  (g = current best known cost, rhs = one-step-lookahead cost) whose disagreement
  signals which nodes need reprocessing. You do not need the formal machinery to
  understand *why* RESQ would want this — just that D* Lite is "smart about which
  part of the graph to re-examine."

## 4. Basic idea

1. Run an A*-like search once to get an initial shortest path and, importantly,
   *keep the internal search state* (don't throw it away).
2. When an edge cost changes (e.g., a road gets blocked), mark only the directly
   affected nodes as "possibly stale."
3. Propagate that staleness only to nodes whose best-known path depended on the
   changed edge — most of the graph, far from the change, needs no rework at all.
4. Recompute a corrected shortest path using only the stale region.
5. If the agent itself has moved (as an ambulance does), adjust the search's notion
   of "start" cheaply too, rather than restarting from the new position blind.

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

Initial shortest path A→D: via C (cost 3), as established earlier.

Now **A→C becomes blocked** (our incident). A full Dijkstra rerun would re-explore
the whole graph from A. D* Lite instead:
- Notices only the edge A→C changed.
- Realizes only nodes whose shortest path used A→C (here: just C, and anything
  reachable only through C) need re-examining.
- Quickly determines C is now only reachable via... nothing else in this tiny
  graph, so C becomes unreachable from A directly; the path A→B→D (cost 9) becomes
  the new answer — but critically, on a *large* graph, D* Lite would only touch the
  small local neighborhood near the blocked edge, not the whole map, giving a real
  speed win proportional to how local the change is.

## 6. How the algorithm chooses a route

Same fundamental "explore cheapest frontier, guided by a heuristic" logic as A*, but
layered with bookkeeping that limits recomputation to the region actually affected
by a graph change, rather than the whole graph.

## 7. What happens if traffic changes?

This is D* Lite's specialty: gradual travel-time updates (not just hard blockages)
can also be handled incrementally — an edge's cost changing from 30s to 45s due to
new congestion information triggers the same "mark stale, propagate locally,
repair" process, without needing to know in advance whether the change is a soft
cost increase or a hard blockage.

## 8. What happens if a road becomes blocked?

This is exactly the scenario D* Lite is designed for — see #5. This maps directly
onto RESQ's own staged demo scenario (`demo_plan()`'s deliberately blocked section)
and, more importantly, onto the *general* case RESQ would need for a production
system: an incident can appear anywhere on the live map, not just at one
pre-selected demo location.

## 9. What inputs would RESQ provide?

Same graph/start/goal as Dijkstra/A*, plus a stream of **edge cost change events**
over time (a road just got blocked; a road just got reported as slower) — which
would come from `fusion/engine.py`'s output changing (a `RoadEstimate.blockage`
flipping to `True`, or `travel_time_s` jumping) as new perception/macro data
arrives.

## 10. What output would RESQ receive?

The same `PathResult`/route shape as Dijkstra/A*, but delivered with much lower
latency on each *update* after the first computation — important if RESQ wants to
react to a newly-detected blockage within, say, the 8-second cue window it already
simulates (`RESQ_CUE_S` in `sim/demo.py`).

## 11. Advantages

- Dramatically faster *replanning* than "rerun Dijkstra from scratch" when only a
  small part of the graph changed — which is the common case for a single new
  incident report.
- Still produces an exact, optimal shortest path (same guarantees as A*, just
  computed incrementally).
- Naturally matches a moving agent (the ambulance itself is progressing along the
  route while new information arrives).

## 12. Disadvantages

- Meaningfully more complex to implement and to get correct than Dijkstra or A* —
  this is real engineering cost, not a minor detail.
- The benefit is proportional to graph size and how localized changes are; on
  RESQ's current tiny demo graph, a full Dijkstra rerun is already sub-millisecond,
  so the *speed* benefit isn't visible yet — the benefit becomes real once RESQ's
  map grows or updates become frequent (e.g., continuous live traffic feeds).
- Requires maintaining persistent search state between queries, which is more
  state to test and reason about than the current "compute once, done" style.

## 13. Computational complexity

Initial computation: same as A*, **O((V+E) log V)**. Each subsequent update: proportional
to the number of nodes whose shortest-path estimate is actually affected by the
change, which is typically much smaller than V — often close to O(k log V) for a
localized change affecting k nodes, though worst case (a change affecting the whole
graph) can still cost as much as a fresh search. Space: O(V+E) plus the persistent
priority-queue/consistency bookkeeping.

## 14. Real-time suitability

Excellent for a system that receives a *stream* of small updates (live traffic,
new incidents) — precisely RESQ's long-term intended data flow
(macro feed + perception observations arriving continuously). Overkill for RESQ's
*current* one-shot demo, valuable for where RESQ says it's headed.

## 15. Emergency ambulance suitability

High conceptual fit: an ambulance needs to react quickly to new incident
information while already en route, and D* Lite is explicitly built for "agent is
moving, world is changing, need fast updates" — arguably the single best match, on
paper, to RESQ's stated mission (README: "combines traffic forecasting... and
safety-constrained corridor control").

## 16. Explainability

Same exactness/transparency as A* for the *final* path. The *incremental update
process* is harder to explain intuitively than "search outward from scratch," but
this doesn't compromise the explainability of the *route itself* — you can always
recompute from scratch as an audit/sanity check and get the identical answer.

## 17. Implementation difficulty

High relative to Dijkstra/A* — this is the most complex pathfinding candidate in
this research set to implement correctly. This is exactly why it is classified as
an important but *secondary* addition, not the first thing to build.

## 18. How it fits the existing RESQ architecture

Would replace the "rerun `RoadNetwork.route` from scratch on every control change"
pattern currently in `sim/demo.py` with a persistent, incrementally-updated search
object. This is a bigger architectural change than A* (which is a drop-in), because
it requires keeping search state alive across snapshot updates rather than
recomputing statelessly each time.

## 19. Where it would sit in the pipeline

PATHFINDING component, specifically the *replanning* sub-responsibility — it reacts
to changes originating in the COST MODEL / FUSION layer (a `RoadEstimate` changing)
and produces an updated route, without needing the fusion/decision layer to know
anything about how the pathfinder is internally organized.

## 20. Simple pseudocode

```text
# Conceptual sketch, not the full formal LPA*/D* Lite algorithm
initialize():
    run A* once from start to goal; remember search state

on_edge_cost_changed(edge, new_cost):
    mark edge.target as "inconsistent"
    propagate inconsistency to any node whose best path used this edge
    re-run the priority-queue processing loop, but ONLY over inconsistent nodes,
        until no inconsistent nodes remain
    (this re-uses all still-valid g-values elsewhere in the graph, unlike a
     full rerun)

on_agent_moved(new_start):
    update the search's notion of "start" and heuristic reference point
    cheaply, without discarding the rest of the graph's search state
```

## 21. What I should remember for viva

- D* Lite solves a **different problem** than Dijkstra/A*: not "find a path" but
  "keep a path optimal cheaply as the world changes."
- It is built on LPA* (fixed start, changing costs) extended for a **moving**
  start (the ambulance itself moving).
- It gives the same optimal answer as rerunning A* from scratch — it's a
  performance/reactivity technique, not a different notion of "best."
- This is the algorithm most directly relevant to RESQ's *incident/blockage*
  scenario at scale, even though RESQ's current tiny demo doesn't yet need its
  speed benefit.

## 22. One practical example

If RESQ's perception pipeline detects a new blockage mid-journey (as it already
simulates via the "hidden accident" control), a production system with D* Lite
would update the ambulance's route in the time proportional to the size of the
affected local neighborhood — not by re-searching the whole city map — which
matters if RESQ's map grows well beyond its current small demo extract.

## What you should understand before reading the next file

You should be able to state, in one sentence, the difference between "recomputing a
route" (Dijkstra/A*, every time) and "repairing a route" (D* Lite, incrementally).
Next: `TIME_DEPENDENT_ROUTING.md`, which addresses a related but distinct idea —
costs that vary by *time of day*, not just by sudden events.
