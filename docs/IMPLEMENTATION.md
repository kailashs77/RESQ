# ResQ implementation guide

## Delivered system

The repository now runs the full software workflow without external simulators:

1. A seeded fast-mode scenario generates traffic, incident, sensor, and signal conditions.
2. Crowd probes can be sampled with adoption, GPS noise, dropout, and upload lag.
3. Historical and graph-smoothed forecasting baselines predict edge speeds.
4. Perception converts tracked vehicles and LiDAR clusters into queue/blockage observations.
5. Confidence-aware fusion produces the travel-time estimate used by routing.
6. Route hysteresis prevents switches unless both the 10-second and 5% thresholds are met.
7. The deterministic signal state machine preserves minimum green, amber, and all-red.
8. B0–B5 experiments are recorded in SQLite and aggregated with p95 and 95% confidence intervals.
9. FastAPI publishes controls, snapshots, adapter status, experiments, results, and ghost replays.
10. The React app provides functional Live, Replay, Cab, and Results workspaces.

## Repository map

| Area | Purpose |
|---|---|
| `schema/` | Versioned contracts shared by all modules and the browser |
| `sim/` | Live demo, probe source, Chennai network builder, SUMO configuration |
| `forecast/` | Graph WaveNet training, checkpoint runtime, baselines, travel-time adapter |
| `perception/` | Tracks, blockage detection, prediction, LiDAR clustering |
| `fusion/` | Inverse-variance travel-time fusion and blockage override |
| `routing/` | Time-dependent shortest path and reroute policy |
| `corridor/` | Fail-safe signal state machine |
| `experiments/` | Seeded B0–B5 × scenario runner |
| `recorder/` | SQLite run store, statistics, synchronized ghost replay |
| `adapters/` | SUMO/TraCI, CARLA sensors, and Google Routes v2 |
| `api/` | REST, WebSocket, static production app hosting |
| `web/` | React, TypeScript, MapLibre control room |

## API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Process and schema health |
| GET | `/api/adapters` | External integration readiness |
| GET | `/api/snapshot` | Current normalized network state |
| PATCH | `/api/controls` | Accident, feed, camera, and adoption controls |
| POST | `/api/reset` | Reset the live scenario |
| WS | `/ws/live` | Five-hertz live snapshot stream |
| POST | `/api/experiments/run` | Execute and persist a requested matrix |
| GET | `/api/results` | Aggregated recorded results |
| GET | `/api/replays` | Available replay catalogue |
| GET | `/api/replays/ghost` | Synchronized B0/B1/B5 replay |
| GET | `/api/forecast` | Corridor model card, held-out metrics, benchmark evidence |
| GET | `/api/forecast/segments` | Current speed prediction per monitored segment |
| GET | `/api/forecast/geometry` | Monitored-segment shapes for the map |
| GET | `/api/forecast/series/{segment_id}` | History, forecast band, held-out truth |
| GET | `/api/forecast/benchmarks` | METR-LA and PEMS-BAY test reports |

Interactive OpenAPI documentation is available at `/docs` while the server runs.

## External modes

The three external adapters are implemented but activate only when their dependencies exist:

- SUMO: run `sim/chennai/build_network.py`, manually verify the imported network, then pass
  `sim/chennai/chennai.sumocfg` to `SumoTraCIAdapter`.
- CARLA: install the Python API matching the CARLA 0.9.x server. `CarlaSensorRig` enables
  synchronous 20 Hz mode and attaches a 1280×720 RGB camera plus 32-channel, 70 m LiDAR.
- Google Routes: set `GOOGLE_ROUTES_API_KEY`. The server requests traffic-aware alternatives,
  static and live duration, encoded geometry, and traffic speed intervals at runtime only.

These adapters intentionally fail with `AdapterUnavailable` rather than silently substituting
data. `/api/adapters` tells the UI and operator exactly which modes are ready.

## Research integrity

- Every stochastic result includes a seed and baseline identifier.
- Baselines toggle only their documented capabilities.
- Google data is never used as training history.
- Targets in the planning document remain targets, not claimed measured outcomes.
- Results from the built-in mesoscopic runner validate software integration; final reported
  transport claims should come from the manually verified Chennai SUMO network with 20+ seeds.
- Conflicting-green violations are explicitly recorded and must remain zero.
