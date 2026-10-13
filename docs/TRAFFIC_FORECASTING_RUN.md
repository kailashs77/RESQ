# Traffic forecasting runbook

The ResQ application still uses the lightweight forecasting baselines. This training pipeline
produces graph-specific Graph WaveNet checkpoints and benchmark reports; California sensor IDs do
not map to Chennai OSM road edges.

## Data provenance

Place these four files in `data/raw/`:

| File | Source |
|---|---|
| `METR-LA.csv` | [Zenodo record 5146275](https://zenodo.org/records/5146275) |
| `PEMS-BAY.csv` | [Zenodo record 5146275](https://zenodo.org/records/5146275) |
| `adj_mx_bay.pkl` | [Zenodo record 5146275](https://zenodo.org/records/5146275) |
| `adj_mx.pkl` | [DCRNN sensor graph](https://github.com/liyaguang/DCRNN/tree/master/data/sensor_graph) |

The Zenodo MD5 checksums are `d3adaa79856bf610f25558fc242c7190`,
`c8dea58987a5882e946217c22fdb8256`, and `55d25daceac847312b7748c59ded6f77`,
respectively. The loader reads the graph pickles only during preparation. Use only the documented
public artifacts; pickle must not be accepted from arbitrary runtime callers.

The generated manifests under `data/processed/` record SHA-256 hashes, speed unit, sensor count,
train-only scaler, and split bounds. Raw files, processed files, and weights are gitignored.

## Prepare and train

Install the `forecast` optional dependencies in a dedicated Python environment. On this Windows
RTX 4050 laptop, the ordinary PyPI PyTorch wheel is CPU-only, so install the CUDA wheel from the
[official PyTorch index](https://download.pytorch.org/whl/cu130) after the optional dependencies.

```powershell
py -3.14 -m venv .venv-forecast
.\.venv-forecast\Scripts\python.exe -m pip install -e ".[forecast]"
.\.venv-forecast\Scripts\python.exe -m pip install --force-reinstall --no-deps torch==2.14.0+cu130 --index-url https://download.pytorch.org/whl/cu130
.\.venv-forecast\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
```

Use `.\.venv-forecast\Scripts\python.exe` in place of `python` below to reproduce this machine's
runs. CPU training is supported, though much slower.

```powershell
python -m forecast.training.cli prepare METR-LA
python -m forecast.training.cli prepare PEMS-BAY
python -m forecast.training.cli baseline METR-LA
python -m forecast.training.cli baseline PEMS-BAY
python -m forecast.training.cli train METR-LA --seed 42 --batch 32 --epochs 100 --patience 15
python -m forecast.training.cli train PEMS-BAY --seed 42 --batch 32 --epochs 100 --patience 15
```

Repeat the two `train` commands with seeds 43 and 44 for the planned three-seed comparison. Each
run saves the best validation checkpoint, metadata, calibration, and a separate test report under
`artifacts/forecast/<dataset>/`. The test split is evaluated only after training has selected its
checkpoint from the first chronological half of validation. The second half calibrates uncertainty.

The input is 12 five-minute observations; the output is 12 future steps. Metrics cover 5, 15,
30, and 60 minutes. Zeros and nonfinite speeds are masked as missing. PEMS-BAY includes one
daylight-saving clock jump; preparation records it, and training excludes windows that cross it.

The current Graph WaveNet implementation uses the official distance graph in both directions and
an adaptive learned graph. Model inputs include normalized speed, time of day, day of week, and an
observation mask. It outputs point speed estimates in mph. Uncertainty calibration and Chennai
segment mapping are the next integration steps described in
[the analysis plan](TRAFFIC_FORECASTING_PLAN.md).

## The Chennai corridor checkpoint

The two public benchmarks validate the model class on Californian freeway loop
detectors. They cannot establish Chennai accuracy, and their sensor graphs do not map
onto the study area, so the model that the decision brain actually uses is trained on
its own dataset: repeatable SUMO runs of the Nungambakkam network.

```powershell
.\.venv\Scripts\python.exe -m sim.chennai.forecast_dataset --days 21 --segments 120 --workers 6
.\.venv-forecast\Scripts\python.exe -m forecast.training.cli baseline CHENNAI-SIM
.\.venv-forecast\Scripts\python.exe -m forecast.training.cli train CHENNAI-SIM --seed 42
```

`scripts/forecast.ps1` runs the whole sequence, benchmarks included.

How the dataset is made:

1. Two peak hours are simulated to rank edges by the traffic they actually carry.
2. The monitored set is every edge of the SUMO ambulance route plus the busiest
   remaining edges of at least 40 m, in that order.
3. The segment graph is a thresholded Gaussian kernel over forward driving distance,
   the same construction the DCRNN sensor graph uses, so the matrix is directed.
4. Each simulated day generates its own trips from a diurnal insertion-rate profile
   with weekday and weekend levels plus per-day and per-hour variation, then records
   five-minute mean speeds per monitored segment.
5. Days are laid out as one continuous calendar starting on a Monday, so the
   time-of-day and day-of-week inputs are meaningful, and the same chronological
   70/10/20 split applies.

Two conventions are worth stating plainly. An unoccupied segment is recorded at its
free-flow speed limit rather than as a missing reading, because in the simulator an
empty road genuinely means free flow; the share of intervals that carried vehicles is
stored in the manifest as `vehicle_sampled_fraction`. And every number in this dataset
comes from the microsimulation, so accuracy measured on it is a statement about the
simulator, not about measured Chennai traffic.

## How the model reaches the decision

`forecast/runtime.py` loads the corridor checkpoint, its calibration and the dataset,
then replays the chronologically final 20 % of the histories — windows that were never
used to fit or to select the checkpoint. One real second advances the replay by one
simulated minute, so the interface can show a live forecast whose ground truth is
already known.

A SUMO edge imported from OpenStreetMap keeps its way identifier, optionally with a
`#part` suffix and a `-` prefix for the reverse direction. `sim/chennai/pricing.py`
uses that to attribute a segment forecast back to the OSM edges of the same way and
direction in the routing graph, so each candidate route is priced with predicted
speeds at the horizon closest to its own arrival time. Roughly four fifths of both
demonstration routes are covered; the rest keeps its assumed free-flow time and the
covered share is reported rather than hidden.

From there the existing pipeline is unchanged: `forecast/adapter.py` turns a predicted
speed and its calibrated interval into a macro `RoadObservation`, `fusion/engine.py`
combines it with the staged camera detection by inverse variance, and `routing` decides
whether the saving justifies a switch. A high-confidence blockage still overrides the
forecast, which is the point of the staged incident.

If no corridor checkpoint is present the API stays up, `/api/forecast` reports
`available: false` with the commands needed to build one, and the demonstration falls
back to its previous staged travel times.

| Endpoint | Purpose |
|---|---|
| `GET /api/forecast` | Model card, held-out metrics, benchmark evidence |
| `GET /api/forecast/segments?horizon=15` | Current prediction per monitored segment |
| `GET /api/forecast/geometry` | Monitored-segment shapes for the map |
| `GET /api/forecast/series/{segment_id}` | History, forecast band and held-out truth |
| `GET /api/forecast/benchmarks` | METR-LA and PEMS-BAY test reports |

The Forecast page in the web client consumes all five.
