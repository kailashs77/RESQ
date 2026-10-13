# 03 — Graph Theory, Just Enough for RESQ

This file teaches only what you need to read the algorithm files. No proofs, no
extra jargon.

## 1. A graph is a map of "things" and "connections"

A **graph** is two lists:
- A list of **nodes** (the "things") — for roads, a node is a junction.
- A list of **edges** (the "connections") — for roads, an edge is a stretch of road
  between two junctions.

That's it. Everything else is refinement on top of this.

## 2. Directed vs undirected

An **undirected** edge can be used in either direction (like a two-way street).

A **directed** edge can only be used in the direction it points (like a one-way
street). Road networks are directed graphs in general, because one-way streets
exist. RESQ's graph is directed: look at `sim/chennai/roads.py` lines 90-98 — it
adds an edge `a → b` only "if oneway != '-1'", and an edge `b → a` only "if oneway
not in {'yes','1','true'}". A two-way street becomes **two** directed edges (one
each way); a one-way street becomes **one** directed edge.

## 3. Weighted graph

A **weighted graph** attaches a number (the "weight" or "cost") to every edge. In
RESQ this number is travel time in seconds (`RoadEdge.travel_s` /
`RoadEdge.base_travel_time_s`), computed as `length_m / speed_mps`.

## 4. Adjacency

Two nodes are **adjacent** if there is a direct edge between them. A graph is
usually stored as an **adjacency list**: for every node, the list of edges leaving
it. That is literally the data structure RESQ uses:
`self.adjacency: dict[str, list[RoadEdge]]` in both `sim/chennai/roads.py` and
`routing/engine.py`.

## 5. Path, cost, source, destination

A **path** is a sequence of edges that connects a **source** node to a
**destination** node, each edge's end matching the next edge's start.

The **cost of a path** is the sum of the weights of its edges. In RESQ: total
travel time in seconds.

## 6. Connected graph

A graph is **connected** if you can reach every node from every other node (ignoring
direction, for undirected; for directed, this needs care — see "strongly
connected" below, but you don't need that term for RESQ). RESQ's `demo_plan()`
function explicitly checks this: if no route exists between the origin and
hospital, it raises `RuntimeError("...disconnected in the road graph")`
(`sim/chennai/roads.py` line 172).

## 7. A tiny fictional road network

```text
        6
   A ------> B
   |         |
  2|         |3
   v         v
   C ------> D
        1
```

Read this as: A→B costs 6, A→C costs 2, B→D costs 3, C→D costs 1. All directed
(one-way), matching RESQ's style.

**Path 1: A → B → D.** Cost = 6 + 3 = **9**.
**Path 2: A → C → D.** Cost = 2 + 1 = **3**.

Path 2 is cheaper. A shortest-path algorithm explores outward from A, keeping track
of the *cheapest known cost to reach each node so far*, and only updates that
cost if it finds something cheaper — this is exactly what Dijkstra's algorithm
automates (full mechanics in `algorithms/DIJKSTRA.md`). At no point does the
algorithm need to know the *whole* graph's structure in advance; it discovers it by
following edges outward from the source, always expanding the cheapest known
frontier node next.

**If road A→C becomes blocked** (our incident scenario), the algorithm simply
cannot use it, and must fall back to A→B→D at cost 9 — this is precisely what
`RoadNetwork.route(..., blocked=frozenset({...}))` does in RESQ: blocked edges are
skipped entirely (`sim/chennai/roads.py` line 116-117), not merely made expensive.

## 8. How a real OSM road network becomes this graph

1. `scripts/build_road_graph.py` downloads raw OSM XML for a bounding box: `<node>`
   elements (lat/lon points) and `<way>` elements (ordered lists of node references
   representing a road, plus tags like `highway`, `oneway`, `name`).
2. It filters to **drivable** highway types (motorway, primary, residential,
   service, etc. — see the `DRIVABLE` set) and excludes private/no-access roads.
3. It writes a compact JSON: `nodes` (id → [lat, lon]) and `ways` (id, ordered node
   list, name, highway type, oneway flag).
4. At runtime, `RoadNetwork.__init__` (`sim/chennai/roads.py`) turns each **way**
   (which may span many nodes) into a chain of **individual edges**, one per
   consecutive node pair, respecting the `oneway` tag to decide whether to add the
   forward edge, the backward edge, or both. Each edge gets a computed length
   (haversine distance, `distance_m`) and travel time (length / assumed speed).

This is the general recipe any OSM-based router uses (real tools like OSRM,
GraphHopper, and OSMnx do the same conceptual conversion, with far more nuance
around turn restrictions, elevation, lane count, etc., which RESQ's demo
deliberately does not model yet — see `00_PROJECT_UNDERSTANDING.md` §12).

## 9. Connecting every concept back to RESQ

| Concept | RESQ's concrete instance |
|---|---|
| Node | An OSM junction id, e.g. `"2145..."` in `roads.json["nodes"]` |
| Edge | One `RoadEdge`, e.g. `f"{way_id}:{index}:+"` |
| Directed graph | `RoadNetwork.adjacency` respecting `oneway` |
| Weighted graph | Edge weight = travel time in seconds |
| Path | A tuple of edge ids, e.g. `RoadPath.edges` |
| Source / destination | `ORIGIN` / `HOSPITAL` constants (fixed lat/lon points) |
| Connected graph | Verified implicitly — `demo_plan()` errors if not |
| Blockage as constraint | `blocked: frozenset[str]` param to `RoadNetwork.route` |

## What you should understand before reading the next file

You should be able to draw a 4-node directed weighted graph on paper, mark a source
and destination, compute two candidate path costs by hand, and say which is
shorter. You should also be able to explain, in one sentence, how RESQ turns OSM's
raw `<way>` XML into the `adjacency` dictionary its router actually searches. Next:
read `algorithms/DIJKSTRA.md`.
