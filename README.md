# ResQ

## Priority 1 routing review

The **Brain** workspace now searches the full checked-in directed Chennai road graph
with A*, consuming fused synthetic baseline forecasts on monitored corridors and
static weights elsewhere. Closures are excluded from search. A 10-second **and**
5-percent hysteresis margin prevents small ETA changes from switching routes;
closures override this policy. Mid-edge replanning preserves the traversed segment.
If no path exists, the vehicle stops and the API returns an explicit `no_path` decision.

The dashboard includes **Routing Decision**, a measured A*/Dijkstra comparison and
a review report with an accident overlay and per-edge connectivity proof. These
measurements are separate from the formula-based Results page. D* Lite is future work.

Generate the evidence from the repository root:

```powershell
uv run python -m experiments.routing_evaluation --repeats 100
```

Open `docs/routing-evaluation/routing-review.html`, or use the evidence link on the
Brain page. Read `docs/ROUTING_REVIEW_GUIDE.md` for demonstration steps and claim limits.

ResQ is a research prototype for ambulance routing and traffic-signal preemption.
The current review build is a deterministic demonstration, **not** a validated
traffic simulation or a trained-model result. Its Chennai routes are shortest
paths on a checked-in, directed OpenStreetMap road graph. The incident, detection
cue, queue delay, driving speeds, and signal states are staged inputs.

## Run locally

Prerequisites: Python 3.12 through 3.14 and Node.js. In this workspace, the existing
`.venv` can be used directly; `uv` does not need to be on `PATH`.

```powershell
cd C:\Users\Kailash\RESQ
.\.venv\Scripts\python.exe -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd C:\Users\Kailash\RESQ\web
npm install
npm run dev
```

Open <http://localhost:5173>. In **Live**, expand “Simulation controls” and
inject the staged incident; changing a control restarts that demo run. In
**Replay**, click Play to compare the regular ambulance and ResQ on the same
mapped road graph. The regular ambulance incurs an assumed 120-second incident
queue; ResQ takes a connected bypass after an assumed 8-second cue. The replay
shows its assumptions beside the map. Map tiles require internet, but the local
vector road overlay and routing data are checked in.

If `.venv` is missing, install `uv` and run `uv sync` from the project root, or
create a Python virtual environment and install the project with its dev extras.
Alternatively, `npm run build` produces a frontend served by FastAPI at
<http://localhost:8000>.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
cd web
npm run build
```

## Generate synthetic experiment estimates

```powershell
.\.venv\Scripts\python.exe -m experiments.runner --seeds 20
```

The Results page also generates formula-based estimates and stores them in
`runs/resq_green.sqlite3`. These values must not be cited as measured performance.

## SUMO network preview (separate from the review demo)

After installing SUMO, set `SUMO_HOME`, put `netconvert` and `sumo-gui` on `PATH`,
then run:

```powershell
$env:SUMO_HOME = 'C:\path\to\sumo'
.\.venv\Scripts\python.exe sim\chennai\build_network.py
sumo-gui -c sim\chennai\chennai.sumocfg
```

This generates a separate SUMO road network and random background trips. The
current application does **not** connect the staged ambulance replay to SUMO,
CARLA, real signals, or a trained prediction model. Thus, use the Replay page for
the review demonstration; do not present it as a SUMO validation run.

## Architecture

The target system architecture combines traffic forecasting, ambulance camera
and LiDAR perception, routing, confidence-aware data fusion, signal priority,
and safety-constrained corridor control. The FastAPI backend exposes the
resulting decisions to the driver application, control-room dashboard, and
replay/experiment store.

![ResQ system architecture](docs/system-architecture.png)

The current review build implements the FastAPI backend, driver web app,
deterministic demonstration simulator, routing, fusion primitives, and safety
state-machine components. YOLO inference, live traffic feeds, and full SUMO or
CARLA decision-loop integration remain planned integration work.

```text
checked-in OSM road graph -> directed routing -> staged incident demo -> API -> React
future SUMO/CARLA integration -> observations -> fusion -> route policy -> validation
```

The shared contract is in `schema/models.py`. The road extract was downloaded
from OpenStreetMap; map data is © OpenStreetMap contributors under ODbL 1.0.
The source and refresh time are recorded in `sim/chennai/roads.json`. To refresh
the extract, run `.\.venv\Scripts\python.exe scripts\build_road_graph.py`, then
rerun tests and visually review the paths. The demo router respects mapped
one-way streets but does not yet model turn restrictions, lane rules, or closures
beyond closures explicitly supplied to the Brain workspace. The Live/Replay
demonstration still uses its precomputed incident section.

See `docs/IMPLEMENTATION.md` for the complete module map, API, adapter setup, and
research-integrity constraints. SUMO map generation instructions are under
`sim/chennai/README.md`.
