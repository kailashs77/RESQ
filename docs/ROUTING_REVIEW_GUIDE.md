# Priority 1: routing evaluation and dynamic replanning

Your fork is `kailashs77/RESQ`, whose GitHub parent is `JaydonJP/RESQ`.
The existing local checkout is `C:\Users\Kailash\RESQ`. The routing work lives
on the `routing-evaluation-replanning` branch.

## Evidence for each review requirement

| Requirement | Implementation and evidence |
|---|---|
| Working A* | Shared exact search in `routing/engine.py`, used by `RoadNetwork.route` and the Brain mission. Differential tests compare costs against Dijkstra and an independent Bellman-Ford oracle. |
| Comparison with Dijkstra | `experiments/routing_evaluation.py` measures actual queries, elapsed computation, expansions and travel cost. Six scenarios, 100 repetitions for each algorithm, five warmups and seeded randomized algorithm order. |
| Road closure | Closure IDs are excluded before edge cost evaluation. The original corridor closes in both mapped directions. The open bypass has a per-edge proof. |
| Predicted edge weights | Fused output from the existing synthetic historical/spatial-temporal baselines changes the searched path. Static weights cover unmonitored edges. A separate FIFO experiment changes route with departure time. |
| Hysteresis | Both 10s and 5% saving must hold. The noisy ETA sequence produces 41 switches without hysteresis and one with it. A mission-level test verifies retention for small forecast improvements. |
| No path | Closing origin exits returns `no_path`, no active route, a null route ID and zero vehicle speed. Removing closures resumes routing. |
| Explainability | The Brain Routing Decision panel shows original roads, incident, decision, reason, selected roads, algorithm, weight source, thresholds and connectivity. |
| Road connectivity | Every route checks node/edge counts, actual directed adjacency, open edges, ordered endpoints and geometry. Mid-edge replanning preserves the traversed edge and vehicle position. |

Open `docs/routing-evaluation/routing-review.html` for the comparison, accident overlay
and complete edge proof. The CSV is ready to copy into review slides. JSON stores
all 1,200 timed samples, graph/source fingerprints, scenario inputs and proofs.

## Demonstration

1. From the repository root, start the backend with
   `uv run uvicorn api.main:app --host 127.0.0.1 --port 8000`.
2. After `npm run build` in `web`, open `http://127.0.0.1:8000` and select **Brain**.
3. Click **Restart mission**, then disable both forecasters to show static A* routing.
4. Enable **Inject simulated road closure**. Show the accident marker, red original
   corridor and green connected detour. The decision explains that feasibility
   overrides the ETA switching margin.
5. Restart the mission with both forecasters enabled and toggle **Inject forecast
   congestion scenario**. Show the new selected path and its switching reason in
   the decision log. This is synthetic baseline forecast output.
6. Scroll to **Routing evaluation**. Compare measured milliseconds, nodes and costs,
   then open the report and expand the per-edge proof.
7. For the no-path demonstration, use `/api/brain/controls` with `blocked_edge_ids`
   covering all outgoing edges of the current node. The automated API test provides
   a repeatable example without guessing edge IDs. Send an empty list to reopen them.

The vehicle advances along mapped edges at assumed static speeds. Updating controls
does not teleport or restart it. An incident can stop an ambulance already on the
closed segment. Restart the mission for a clean origin-to-hospital comparison.

## Review wording

“My part now implements A* and compares it experimentally with Dijkstra on the same
directed Chennai road graph. Both return the same optimal travel-time cost for the
tested inputs. A* reduces node exploration, while the timing table shows the
overhead and limits of its heuristic. Simulated closures remove edges, and
rerouting checks connectivity before publishing an active path. Small forecast
improvements must pass both switching margins. If the destination becomes
unreachable, we report no path and stop the vehicle. The dashboard explains each
decision. D* Lite remains a future improvement.”

## Limits that affect the conclusion

- Wall-clock computation is measured. Road speeds, congestion and incident inputs
  are illustrative. Route travel cost is not measured ambulance journey time.
- Brain uses an immutable forecast weight snapshot per query. The core's separate
  time-dependent callback experiment evaluates costs at edge entry time. Do not
  describe the dashboard as horizon-calibrated arrival-time Graph WaveNet routing.
- The checked-in extract has 3,442 nodes and 6,022 directed edges. Connectivity
  respects mapped direction but excludes turn restrictions, lane rules and legal
  access permissions. It does not certify real navigation.
- Forecasters monitor the union of the original and demonstration bypass corridors.
  Other graph edges use static fallback. Trained-model/live perception integration,
  live closures and end-to-end SUMO validation are outside this implementation.
- Signal checkpoints are virtual. Existing priority requests retain the controller's
  expiration, maximum preemption and recovery constraints. No real signals actuate.
- The original Live/Replay and formula-based B0–B5 Results remain demonstrations.
  Use the new routing evidence when claiming algorithm performance.
- Genetic algorithms, ant colony optimization and reinforcement learning were
  considered qualitatively. This work does not claim to have benchmarked them.
- D* Lite is not implemented or benchmarked. Fresh A* searches perform replanning.

## Reproduction

Run the repository's `scripts/verify.ps1`, then generate measured artifacts:

```powershell
uv run python -m experiments.routing_evaluation --repeats 100
```

The report records machine/Python details, a graph SHA-256, source fingerprints,
base commit and exact timing boundaries. Timing varies between machines and runs.
No timing assertion relies on A* always being faster.

## Algorithm references

An admissible heuristic is necessary for the A* optimality guarantee. The
[official NetworkX documentation](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.shortest_paths.astar.astar_path.html)
states the consequence of overestimating remaining cost. Our road adapter computes
a lower cost per metre from the actual open-edge weights, including fast predictions.

Time-dependent exact search assumes FIFO arrival functions. See Brian Dean's
[Shortest Paths in FIFO Time-Dependent Networks](https://people.computing.clemson.edu/~bcdean/paper23.html).
The controlled experiment uses a constant cost until time 400s, followed by a
nondecreasing congestion ramp, so entering later cannot produce earlier arrival.

D* Lite reuses incremental search state. See Likhachev and Koenig's
[Incremental Replanning for Mapping](https://www.cs.cmu.edu/~maxim/files/DLiteMap_iros02_abs.html).
The current implementation uses fresh searches and makes no incremental-speed claim.
