# Traffic forecasting analysis and implementation plan

Date: 2026-09-22

## Decision

Implement **Graph WaveNet** as ResQ's first trained forecasting model. Train one checkpoint on
METR-LA and another on PEMS-BAY; do not concatenate the datasets or pretend that their sensor
nodes belong to one graph. Keep the existing historical-average and graph-smoothed models as
mandatory baselines. Use STAEformer as the accuracy challenger after the Graph WaveNet pipeline
is reproducible.

The model task is:

- Input: the previous 12 five-minute speed observations (one hour) for every monitored node.
- Output: the next 12 speed observations (one hour) in one forward pass.
- ResQ consumes steps 1, 3, and 6: 5-, 15-, and 30-minute forecasts.
- Step 12 (60 minutes) is retained to compare with published benchmark results.
- Predictions are converted from benchmark mph to m/s at the application boundary and then to
  edge travel time using edge length.

Graph WaveNet is the best engineering trade-off here, not necessarily the newest model. Its
dilated temporal convolutions are parallel, its graph convolution represents propagation between
roads, and its adaptive adjacency can discover dependencies absent from the supplied distance
graph. On the common protocol, STAEformer reports only small gains over Graph WaveNet on METR-LA
and essentially equal performance on PEMS-BAY. The simpler, graph-explicit model is a better first
fit for routing and for the available RTX 4050 Laptop GPU with 6 GB VRAM.

## What the datasets can and cannot establish

| Property | METR-LA | PEMS-BAY |
|---|---:|---:|
| Signal | freeway speed | freeway speed |
| Sensors | 207 | 325 |
| Samples | 34,272 | 52,116 |
| Sampling | 5 minutes | 5 minutes |
| Period | Mar-Jun 2012 | Jan-May 2017 |
| Standard split | chronological 70/10/20 | chronological 70/10/20 |

Use **both**, but as two separate experiments and checkpoints:

- METR-LA is the primary development dataset because it is the more difficult benchmark and is a
  better stress test for congestion and missing readings.
- PEMS-BAY is the independent replication dataset. A conclusion that appears on only one of the
  two datasets is not treated as robust.
- If schedule pressure permits only one initial run, complete METR-LA first without changing the
  pipeline design.

These are California freeway loop-detector datasets. They do not contain Chennai streets,
signals, incidents, ambulance behaviour, weather, or event context. A METR-LA/PEMS-BAY model can
validate the forecasting software and establish a research baseline, but it cannot substantiate
Chennai prediction accuracy.

The checked-in Chennai extract has 3,442 OSM nodes and approximately 6,022 directed routing
edges. It would be wasteful to make every tiny directed edge a dense forecasting node. Chennai
training should later use stable monitored segments or corridor partitions, with SUMO edge speeds
aggregated to those segment IDs. The segment-to-routing-edge mapping must be explicit and
versioned.

## Model shortlist

| Model | Strength | Main limitation | Decision |
|---|---|---|---|
| Persistence / historical average | transparent and difficult to beat at 5 minutes | no learned spatial dynamics | required baseline |
| DCRNN | directed diffusion graph; official pretrained METR-LA and PEMS-BAY models | official runtime is legacy TensorFlow 1.x and recurrent inference | reference predictions only |
| Graph WaveNet | strong accuracy, direct 12-step output, PyTorch, explicit plus learned graph | adaptive node embeddings make checkpoints graph-specific | **primary model** |
| STAEformer | excellent reported benchmark accuracy and concise architecture | quadratic spatial attention; node-specific adaptive embedding; no official checkpoint | challenger |
| OpenCity | released pretrained weights and cross-city zero-shot design | older research stack, 288-step context in released config, limited deployment packaging | transfer research spike |

The published STAEformer comparison reports these representative MAEs:

| Dataset / horizon | Graph WaveNet | STAEformer |
|---|---:|---:|
| METR-LA, 15 min | 2.69 | 2.65 |
| METR-LA, 30 min | 3.08 | 2.97 |
| METR-LA, 60 min | 3.51 | 3.34 |
| PEMS-BAY, 15 min | 1.30 | 1.31 |
| PEMS-BAY, 30 min | 1.63 | 1.62 |
| PEMS-BAY, 60 min | 1.99 | 1.88 |

These published numbers are reference values, not results from ResQ. We will report our own runs,
seeds, environment, and checkpoints.

## Pretrained model conclusion

There are usable pretrained artifacts, but none should be dropped directly into the ResQ runtime:

1. The official DCRNN repository includes pretrained METR-LA and PEMS-BAY configurations and
   checkpoints. They are useful for generating reference predictions and verifying preprocessing,
   but the TensorFlow 1.x environment should remain isolated from this Python 3.14 application.
2. OpenCity publishes a 104 MB OpenCity-Plus checkpoint and is explicitly designed for zero-shot
   traffic prediction. It is the most relevant later experiment for transfer to a new city. Its
   released repository uses an older Python/PyTorch environment, its Hugging Face model card is
   effectively empty, and it still requires a correctly constructed target graph and history. It
   is therefore not a plug-and-play Chennai forecaster.
3. STEP publishes pretrained temporal encoders for these datasets, but a downstream forecasting
   model still has to be trained. It does not eliminate our training work.

For the first milestone, reproduce an official DCRNN prediction file as a pipeline oracle, then
train and deploy our own Graph WaveNet checkpoint. Do not serve the legacy DCRNN process from the
FastAPI application.

## Data and leakage protocol

1. Download raw `metr-la.h5`, `pems-bay.h5`, sensor IDs, locations, and adjacency artifacts into
   `data/raw/`, which is already gitignored. Record source URLs, file hashes, and retrieval date in
   a manifest; do not commit the dataset unless its redistribution terms are verified.
2. Preserve time order. Split first, then form windows within each split. No random split and no
   window may cross a split boundary.
3. Fit speed normalization on the training period only. Apply that scaler unchanged to validation
   and test data.
4. Form `(x, y)` as `[12, N, F] -> [12, N, 1]`. Features are normalized speed, time of day, day of
   week, and an observed/missing mask where the implementation supports it.
5. Treat zero/missing targets using the benchmark's masked metrics. Never silently turn a missing
   reading into a genuine stopped-traffic observation.
6. Keep mph through benchmark training and evaluation for comparability. Convert units only in the
   ResQ adapter and include the unit in checkpoint metadata.
7. Make preprocessing deterministic and cache processed arrays under `data/processed/`.

## Training protocol

Run the two datasets independently with the same protocol:

- Loss: masked MAE over all 12 output steps.
- Optimizer: Adam, initial learning rate `1e-3`, weight decay `1e-4`.
- Stabilization: gradient norm clipping at 5; automatic mixed precision on CUDA.
- Budget: at most 100 epochs with validation-MAE early stopping (patience 15).
- Laptop starting batch: 32; reduce to 16 on out-of-memory. Use gradient accumulation if needed.
- Reproducibility: three seeds for the final comparison; save best validation checkpoint for each.
- Model selection: mean validation MAE over all 12 horizons, not the test set and not only one
  convenient horizon.
- Checkpoint metadata: git revision, dataset hash, split timestamps, scaler, sensor order, graph
  hash, hyperparameters, seed, training versions, and validation score.

The training environment should be an optional forecasting dependency group or a dedicated Python
3.12/3.13 environment. Torch, pandas, NumPy, SciPy, and HDF5 support should not become mandatory
dependencies for the lightweight API/runtime test suite.

## Uncertainty and conversion to route cost

Graph WaveNet is a point forecaster, while ResQ fusion requires a standard deviation. Calibrate it
using validation residuals rather than inventing a constant:

1. Store residual distributions per dataset, node, and horizon.
2. Estimate residual standard deviation with shrinkage toward a horizon-wide estimate when a node
   has insufficient observations.
3. Compute split-conformal absolute-residual quantiles for 80%, 90%, and 95% prediction intervals.
4. Freeze calibration before the test run and report empirical coverage and interval width.
5. For an edge of length `L`, predicted speed `v`, and speed deviation `sigma_v`, use
   `travel_time = L / max(v, v_floor)` and the first-order approximation
   `sigma_time = L * sigma_v / max(v, v_floor)^2`. Apply explicit physical bounds.

This calibrated `sigma_time` becomes `RoadObservation.stddev_s`, so the existing inverse-variance
fusion gives less weight to uncertain long-horizon forecasts. A high-confidence perception
blockage continues to override the macro forecast.

## Evaluation

### Forecast metrics

- Masked MAE, RMSE, and MAPE at 5, 15, 30, and 60 minutes.
- Mean masked MAE across all 12 horizons.
- Metrics by time of day and by congestion regime, not only an overall average.
- Missing-sensor robustness at 10%, 20%, and 40% simulated dropout.
- Peak VRAM plus batch-1 CPU and GPU inference latency.
- Prediction-interval coverage and mean interval width.

MAPE is retained for literature comparison but must not be the only metric because it behaves badly
near zero speed.

### System metrics

- Route ETA MAE after speed-to-travel-time conversion.
- Route-choice agreement with an oracle using realized future speeds.
- Route regret and reroute reaction time in the existing experiment framework.
- Forecast-only, perception-only, fusion, and stale-macro ablations.
- Incident tests must separate an event that is already visible in recent speeds from a genuinely
  unseen event. A speed-only model cannot foresee a newly occurring crash; perception and live
  probes are responsible for detecting it quickly.

### Initial acceptance gates

- Beat persistence and historical average on masked MAE at 5, 15, and 30 minutes on both datasets.
- Reproduce Graph WaveNet reference MAE within 10% under the same split and mask protocol.
- Achieve 87-93% empirical coverage for the calibrated 90% interval on the untouched test split.
- Keep batch-1 GPU inference below 250 ms on the current laptop; inference is cached and refreshed
  on each five-minute macro update rather than run in the 5 Hz UI loop.
- All data, split, unit-conversion, checkpoint-load, and adapter tests pass without a GPU.

## Repository implementation shape

```text
forecast/
  data.py              dataset manifest, loading, chronological windows
  graph.py             sensor/segment ordering and adjacency construction
  metrics.py           masked metrics and calibration coverage
  graph_wavenet.py     model only
  calibration.py       residual scale and conformal intervals
  checkpoint.py        metadata validation and safe loading
  service.py           cached batch inference and ResQ conversion
  cli.py               prepare, train, evaluate, export commands
configs/forecast/
  metr_la.yaml
  pems_bay.yaml
artifacts/forecast/     ignored weights; committed metadata/result summaries only
tests/forecast/         synthetic fixtures; no dataset or GPU required
```

The runtime interface should accept a timestamp, ordered node IDs, recent speeds, observation mask,
and horizon list. It should return speed mean/deviation for each node and horizon. A separate
adapter maps benchmark sensor IDs or Chennai segment IDs to routing edge IDs. The model must never
depend directly on FastAPI or the browser.

## Delivery sequence

1. **Data and baselines:** manifests, deterministic windows, masked metrics, persistence and
   historical-average results, and one official DCRNN reference prediction comparison.
2. **Graph WaveNet:** modern PyTorch implementation, METR-LA training, checkpoint metadata, and
   reproducible evaluation report.
3. **Replication:** PEMS-BAY checkpoint and three-seed comparison.
4. **Uncertainty and ResQ adapter:** conformal calibration, speed/travel-time conversion, cached
   inference, and fusion/routing integration tests.
5. **Challengers:** train STAEformer under the identical data protocol; retain it only if its
   accuracy gain justifies memory/latency cost. Run OpenCity zero-shot as a clearly labelled
   transfer experiment.
6. **Chennai adaptation:** collect repeatable SUMO segment histories, define corridor partitions,
   train or fine-tune on Chennai, and evaluate route regret across held-out seeds and incident
   scenarios. Only this phase supports Chennai-specific forecasting claims.

## Primary references

- [DCRNN paper and official data/checkpoints](https://github.com/liyaguang/DCRNN)
- [Graph WaveNet paper](https://arxiv.org/abs/1906.00121)
- [Original Graph WaveNet implementation](https://github.com/nnzhan/Graph-WaveNet)
- [STAEformer paper](https://arxiv.org/abs/2308.10425)
- [STAEformer official implementation](https://github.com/XDZhelheim/STAEformer)
- [OpenCity paper](https://arxiv.org/abs/2408.10269)
- [OpenCity official repository](https://github.com/HKUDS/OpenCity)
- [OpenCity-Plus checkpoint](https://huggingface.co/hkuds/OpenCity-Plus)
- [BasicTS dataset catalogue and reproducible benchmark toolkit](https://github.com/GestaltCogTeam/BasicTS)
