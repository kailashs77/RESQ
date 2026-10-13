# 01 — The Routing Problem, In Plain Language

This file defines every term you need before any algorithm makes sense, then states
precisely what RESQ needs — and warns you against the beginner mistake of assuming
"routing" just means "shortest distance."

## 1. Terms, defined simply

**Graph** — a collection of *points* connected by *lines*. In routing, points are
places (road junctions), and lines are roads between them. Nothing more mystical
than that.

**Node** — one point in the graph. In RESQ, a node is a road junction or an endpoint
of a mapped road segment (see `sim/chennai/roads.py`, `self.nodes`, built straight
from OSM `<node>` elements' lat/lon).

**Edge** — one line/connection in the graph, joining two nodes. In RESQ, an edge is
one directed stretch of road between two junctions (`RoadEdge` in both
`routing/engine.py` and `sim/chennai/roads.py`).

**Road network (as a graph)** — the whole city's roads represented as nodes + edges.
RESQ's is `sim/chennai/roads.json`: a small, real OSM extract around Thousand
Lights, Chennai.

**Edge weight (a.k.a. edge cost)** — a number attached to an edge representing "how
expensive is it to use this road." It does not have to be distance — it can be
travel time, risk, fuel, or any blend. RESQ's edge weight is a travel time in
seconds, computed as `length_m / speed_mps` (`sim/chennai/roads.py` line 93/97) — or,
in the general router, a *function* of the time you enter the edge
(`routing/engine.py`, `EdgeCost = Callable[[RoadEdge, float], float]`).

**Path** — a sequence of edges connecting a start node to an end node.

**Shortest path** — the path whose total edge weight (not necessarily distance —
depends what "weight" means) is the smallest of all possible paths.

**Travel time** — the real-world time, in seconds, to traverse an edge or a whole
path. In RESQ this is currently derived from a constant assumed speed, not live
telemetry (see `00_PROJECT_UNDERSTANDING.md` §12).

**ETA (estimated time of arrival)** — the predicted travel time added to the current
time; i.e., when the vehicle is expected to arrive.

**Static routing** — computing a path once using fixed, unchanging edge weights
(e.g., plain distance). The plain "Dijkstra on constant speeds" used in
`sim/chennai/roads.py::RoadNetwork.route` is static in this sense (weights don't
change with time-of-day or traffic).

**Dynamic routing** — recomputing or adjusting the path as *new information arrives*
(traffic gets worse, a road closes, a sensor sees a blockage). RESQ's
`should_switch_route` hysteresis (`routing/engine.py`) is a small piece of dynamic
routing logic: it decides *whether* to switch to a newly-better path.

**Time-dependent routing** — a specific, more precise form of dynamic routing where
an edge's cost is a *function of the time you arrive at it* (e.g., a road is
slow at 6pm rush hour but fast at 2am). RESQ's `routing/engine.py::RouteGraph`
is explicitly built this way — its `edge_cost` callback receives
`departure_time_s + elapsed`, i.e., the actual clock time you'd enter that edge, so
the same edge can cost differently depending on when the search reaches it.

**Heuristic** — an *estimate* used to guide a search toward the goal faster, without
necessarily exploring the whole graph. Must not overestimate the true remaining
cost for algorithms like A* to stay correct (this "must not overestimate" property
is called **admissibility**, explained fully in `algorithms/A_STAR.md`).

**Cost function** — the rule that turns "using this edge, at this time, in this
context" into a single number to minimize. In RESQ today the cost function is just
travel time; a richer RESQ would combine several sub-costs (see §on multi-objective
below and `10_GOAT_DECISION_ENGINE.md`).

**Objective function** — the thing the routing algorithm is actually trying to
minimize (or maximize) overall — for a single-criterion problem this is the same as
"the cost function summed along the path." For a multi-criteria problem, the
objective function is how multiple costs get combined into one number (or a
Pareto frontier — see below).

**Constraint** — a hard rule a path must obey, which is *not* traded off against
other goals. Example: "must not use a blocked edge" (RESQ already enforces this —
`RoadNetwork.route(..., blocked=frozenset())` simply removes blocked edges from
consideration, `sim/chennai/roads.py` line 116-117). Contrast with a *cost*, which
is soft (expensive but not forbidden).

**Traffic congestion** — slower-than-free-flow speed on a road due to demand. Not
currently modeled with live data in RESQ; `forecast/models.py` predicts congestion
proxies (mean speed) from historical/recent data.

**Incident / road blockage** — an event that makes a road partially or fully
unusable (accident, flooding, closure). RESQ stages exactly one, deterministically
(`sim/chennai/roads.py::demo_plan`, which picks and blocks a contiguous ~100m
section of the normal route).

**Emergency vehicle priority** — the general idea that an ambulance's routing and
signal treatment should differ from ordinary traffic: it may accept a costlier
detour to avoid an uncertain risk, and it can request a green signal preemptively.
RESQ's `corridor/state_machine.py` implements the signal side of this; the
route-choice side is currently only the hysteresis threshold plus the fusion
blockage override.

**Multi-objective routing** — routing where more than one thing matters at once
(e.g., minimize expected time *and* minimize risk *and* prefer a stable route), and
there is no single "best" path without deciding how to trade those off. See
`algorithms/MULTI_OBJECTIVE_ROUTING.md`.

**Route replanning** — recomputing a route mid-journey because conditions changed.
RESQ's `should_switch_route` decides *when* to accept a replanned route, but the
live demo currently only replans between two precomputed alternatives, not a fresh
graph search mid-trip.

## 2. What RESQ actually needs (do not assume "shortest distance")

A naive beginner assumption is: "routing = find the shortest path by distance."
That is wrong for an ambulance, and it's wrong for RESQ's own architecture. Here is
why each of the following matters, and whether RESQ's current code already reflects
it:

| Concern | Why it matters for an ambulance | Supported today? |
|---|---|---|
| Travel time (not distance) | A longer road that's faster beats a shorter road that's jammed. | **Yes** — both routers already cost by time, not meters. |
| Congestion | Live traffic changes travel time significantly minute to minute. | **Partially** — `forecast/models.py` can estimate it; nothing feeds live congestion into an edge cost yet. |
| Predicted congestion | Congestion 5–10 minutes from now (when the ambulance will actually be on that edge) matters more than congestion right now. | **Only as a baseline formula** (`SpatialTemporalForecaster`), not wired into routing. |
| Road blockage / incident | A blocked edge must be avoided or treated as near-infinite cost. | **Yes, for the one staged incident** — general at runtime, no. |
| Incident information | Fresh, local information (perception) can catch things macro data hasn't caught yet. | **Yes** — this is the entire point of `fusion/engine.py`'s blockage override. |
| Confidence of predictions | Low-confidence data shouldn't be trusted as much as high-confidence data. | **Yes** — `fuse_observations` weights inversely by variance/confidence. |
| Signal priority | Getting a green wave matters as much as the route chosen. | **Yes, separately** — `corridor/state_machine.py`, independent of the pathfinding graph search. |
| Route stability | Switching route every few seconds due to noise is dangerous/confusing. | **Yes** — `should_switch_route` hysteresis. |
| Safety | Some risk (e.g., an uncertain-but-plausible blockage) should be avoided even if "expected" time is similar. | **Partially** — the hard blockage override behaves this way for one case; there's no general safety-margin cost term. |
| Changing road conditions mid-trip | The ambulance should be able to reroute after departure, not just once at the start. | **Time-dependent core exists** (`routing/engine.py`), but the live demo doesn't call it mid-trip today. |

So: RESQ's actual requirement is a **time-dependent, safety-aware, confidence-aware,
stable single-vehicle shortest path problem with hard blockage constraints and a
separate signal-preemption subsystem** — not plain shortest-distance, and (as of
today) not full multi-objective optimization either, though the architecture is
heading that way (see `10_GOAT_DECISION_ENGINE.md`).

## What you should understand before reading the next file

You should be able to explain, without looking back: the difference between a
*constraint* and a *cost*, why "shortest distance" is the wrong mental model for an
ambulance, and what "time-dependent" specifically means (it's about *when* you enter
an edge, not just *whether* traffic is heavy in general). Next: `02_ALGORITHM_CANDIDATES.md`.
