# 40 — Implementation Plan (Research Only — No Code Changed)

> Historical rollout plan. Priority 1 implements shared A*/Dijkstra search,
> forecast-snapshot weighting in Brain, explicit closures, graceful no-path
> handling and experimental evidence. D* Lite (Stage 4) remains future work.
> See `docs/ROUTING_REVIEW_GUIDE.md` for the current implementation and limits.

This describes *how* the recommendation in `30_ROUTING_ALGORITHM_RECOMMENDATION.md`
and `31_FINAL_ROUTING_ARCHITECTURE.md` could eventually be built into RESQ. No code
in this repository has been modified to produce this document.

## Staged rollout, in priority order

### Stage 1 — Wire the existing time-dependent router into the live demo

**Goal**: replace `sim/demo.py`'s two-fixed-route hardcoded logic with a real call
to `routing/engine.py::RouteGraph.shortest_path`, using live `RoadEstimate` data as
edge costs.

- Files likely touched: `sim/demo.py` (build a `RouteGraph` from
  `sim/chennai/roads.py`'s adjacency instead of only using two precomputed paths),
  `routing/engine.py` (possibly extend `RoadEdge`/`RouteGraph` to carry road_id so
  costs can look up a `RoadEstimate`).
- New/changed data flow: `fuse_observations` output per road → an `EdgeCost`
  closure → `RouteGraph.shortest_path`.
- Interfaces: keep `RouteOption` as the output contract; no `schema/models.py`
  changes needed for this stage alone.
- Testing strategy: extend `tests/test_routing.py` with a case exercising a full
  fused-cost-driven search on the Chennai graph, and a regression test asserting
  the "hidden incident" scenario still selects the bypass route with live-computed
  costs (compare with the existing hardcoded-ETA test expectations in
  `tests/test_road_demo.py`).
- Failure cases to test: no path exists after blocking; all roads at equal cost
  (tie-breaking behavior); a road with missing/absent perception data (must fall
  back gracefully to macro-only, per evaluation criterion #13 robustness).

### Stage 2 — Add the DYNAMIC EDGE-COST CALCULATION stage (scalarized multi-objective cost)

**Goal**: introduce the "cost model" layer described in `10_GOAT_DECISION_ENGINE.md`
as its own small, testable module rather than folding weighting logic into
`sim/demo.py` or `fusion/engine.py`.

- New file (proposed): `routing/cost_model.py` (name chosen for consistency with
  existing `routing/` package; final naming is a team decision) containing a
  `RouteCostConfig` (weights) and a function turning a `RoadEstimate` (+ optional
  risk/stability inputs) into a scalar cost, matching `routing/engine.py`'s
  `EdgeCost` signature.
- Files touched: `routing/engine.py` (no change needed if the cost function is
  supplied externally, matching the existing pluggable `EdgeCost` design),
  `sim/demo.py` (use the new cost function instead of ad hoc constants).
- Schema: consider adding an optional `RouteOption.cost_breakdown: dict[str, float]`
  field (extra="forbid" means this must be a deliberate schema change, not silently
  added) so the UI/API can show *why* a route costs what it does — directly
  supporting explainability (criterion #10).
- Testing strategy: unit-test the cost function in isolation (given known
  `RoadEstimate`s and weights, assert the expected scalar); property-test that
  higher risk/lower confidence never *decreases* cost (monotonicity sanity check).
- Failure cases: weight misconfiguration (e.g., negative weight) should be
  rejected at construction, not silently produce nonsense costs — mirrors the
  existing pattern of `RouteGraph`/`FusionConfig` validating inputs eagerly.

### Stage 3 — A* heuristic

**Goal**: speed up `RouteGraph.shortest_path` with a straight-line-distance
heuristic, with zero change to its output.

- Files touched: `routing/engine.py` only — add an optional `heuristic` parameter
  to `shortest_path`, defaulting to `None` (current Dijkstra behavior preserved) to
  avoid breaking existing callers/tests.
- Testing strategy: a differential test — for a range of start/goal pairs, assert
  A*-with-heuristic returns the *identical* path and cost as plain Dijkstra, only
  visiting fewer or equal nodes (instrument a node-visit counter for this
  assertion). This is the cheapest stage to implement and validate.
- Failure cases: an inadmissible/misconfigured heuristic silently producing a
  suboptimal route — the differential test above is specifically designed to catch
  this class of bug.

### Stage 4 — D* Lite incremental replanning

**Goal**: avoid full-graph reruns when a blockage/update arrives, once RESQ's map
or update frequency grows enough to justify the added complexity (see
`21_ROUTING_EVALUATION_CRITERIA.md` #11 — don't adopt preemptively).

- New file (proposed): `routing/incremental.py`, wrapping `RouteGraph` with
  persistent search state and an `on_edge_cost_changed` method.
- Files touched: `sim/demo.py` or a future live-update loop would need to call
  `on_edge_cost_changed` when a new `RoadEstimate` arrives, instead of
  reconstructing the graph/search from scratch.
- Testing strategy: differential testing again — after a sequence of edge changes,
  assert the incrementally-repaired path matches a from-scratch Dijkstra rerun on
  the same final graph state; additionally, benchmark node-touch count to confirm
  the expected locality benefit on a larger synthetic graph (RESQ's current tiny
  demo graph may not show a measurable difference — call this out honestly in any
  report, per the project's stated research-integrity norms in
  `docs/IMPLEMENTATION.md`).
- Failure cases: a change that invalidates a large fraction of the graph (e.g., a
  major arterial closes) should still fall back correctly, even if the "efficient"
  path doesn't save much time in that pathological case.

### Stage 5 — Connect real prediction/perception (largest, longest-term, and already scoped as future work in the repo's own docs)

- Replace `forecast/models.py`'s baselines with a trained model behind the same
  `SpeedForecast`-shaped interface (`README.md`/`docs/REVIEW_II_VIVA_NOTES.md`
  already flag this as future work — not new scope invented by this plan).
- Replace `perception/pipeline.py`'s manually-constructed `TrackedObject` inputs
  with real YOLO+tracker output from `adapters/carla_sensors.py` or real hardware.
- Neither of these requires touching `routing/` or `corridor/` at all, **if** Stage
  1-2's contracts are respected — this is the concrete payoff of the architecture
  in `31_FINAL_ROUTING_ARCHITECTURE.md`.

## Simulation strategy

- Continue using the fast deterministic demo (`sim/demo.py`) for rapid iteration
  and CI, exactly as RESQ already does — this plan does not propose removing it.
- Use the existing but currently-unwired SUMO adapter (`adapters/sumo_traci.py`)
  for larger-scale, more realistic validation once Stage 1-2 are stable — this
  matches the project's own documented "future work" roadmap (§5, Member 1's
  future work list in `docs/REVIEW_II_VIVA_NOTES.md`).
- Any new randomized/statistical tests (e.g., the D* Lite differential test) must
  use a fixed seed, consistent with the project's existing reproducibility norms
  (`experiments/runner.py`'s seeded design).

## Metrics

Reuse RESQ's existing `RunMetrics` schema fields where possible
(`ambulance_travel_time_s`, `route_switches`, `reroute_reaction_time_s`,
`route_regret_s`) rather than inventing new ones — `route_regret_s` (difference
from an all-knowing oracle) is already exactly the right metric for judging how
close the new cost-model-driven routing gets to optimal-with-hindsight behavior.

## What you should understand before reading the next file

You should be able to list the five stages in order and explain, for each, what
*minimum* prerequisite from an earlier stage it depends on (e.g., Stage 3's A*
heuristic can be added independently of Stage 2's cost model, but Stage 4's D* Lite
benefits most once Stage 1 is wired to real, changing data). Next:
`50_BEGINNER_STUDY_ORDER.md`.
