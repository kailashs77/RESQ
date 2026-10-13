"""Core regression checks for the optional forecasting pipeline."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from forecast.training.calibration import calibrate, coverage_90  # noqa: E402
from forecast.training.data import TrafficData, split_bounds  # noqa: E402
from forecast.training.graph_wavenet import GraphWaveNet  # noqa: E402
from forecast.training.metrics import masked_mae  # noqa: E402
from forecast.training.service import TrainedGraphForecaster  # noqa: E402


def _tiny_data() -> TrafficData:
    length, nodes = 240, 4
    timestamps = np.arange(length, dtype=np.int64) * 300_000_000_000
    timestamps[100:] += 3_600_000_000_000
    speed = np.full((length, nodes), 30.0, dtype=np.float32)
    observed = np.ones((length, nodes), dtype=bool)
    observed[20, 0] = False
    speed[20, 0] = 0.0
    return TrafficData(
        speed=speed,
        filled=np.where(observed, speed, 30.0),
        observed=observed,
        timestamps=timestamps,
        ids=[str(i) for i in range(nodes)],
        adjacency=np.eye(nodes, dtype=np.float32),
        mean=30.0,
        std=5.0,
    )


def test_windows_stay_inside_split_and_skip_timestamp_gap() -> None:
    data = _tiny_data()
    train = data.windows("train")
    _, train_end = split_bounds(len(data.speed))["train"]
    assert all(start + 24 <= train_end for start in train.starts)
    assert all(not (start <= 99 < start + 23) for start in train.starts)
    x, y, mask = train[0]
    assert x.shape == (12, 4, 4)
    assert y.shape == mask.shape == (12, 4)


def test_graph_wavenet_has_twelve_outputs_and_gradients() -> None:
    model = GraphWaveNet(torch.eye(4), residual_channels=8, skip_channels=16, end_channels=32)
    output = model(torch.randn(2, 12, 4, 4))
    assert output.shape == (2, 12, 4)
    loss = masked_mae(output, torch.zeros_like(output), torch.ones_like(output))
    loss.backward()
    assert torch.isfinite(model.node_left.grad).all()


def test_calibration_shapes_and_coverage() -> None:
    target = np.zeros((120, 12, 2), dtype=np.float32)
    prediction = np.ones_like(target)
    mask = np.ones_like(target)
    calibrated = calibrate(prediction, target, mask)
    q90 = np.asarray(calibrated["q90_mph"])
    assert q90.shape == (12, 2)
    assert coverage_90(prediction, target, mask, q90) == 1.0


def test_checkpoint_inference_rejects_wrong_sensor_order(tmp_path) -> None:
    ids = [str(i) for i in range(4)]
    weights = tmp_path / "weights.pt"
    metadata = tmp_path / "metadata.json"
    calibration = tmp_path / "calibration.json"
    torch.save(GraphWaveNet(torch.eye(4)).state_dict(), weights)
    metadata.write_text(
        json.dumps(
            {
                "sensor_ids": ids,
                "speed_unit": "mph",
                "input_steps": 12,
                "output_steps": 12,
                "data_manifest": {"train_mean": 30.0, "train_std": 5.0},
            }
        )
    )
    calibration.write_text(json.dumps({"stddev_mph": [[2.0] * 4] * 12}))
    service = TrainedGraphForecaster(weights, metadata, calibration)
    speeds = np.full((12, 4), 30.0, dtype=np.float32)
    observed = np.ones((12, 4), dtype=bool)
    with pytest.raises(ValueError, match="sensor order"):
        service.predict(speeds, observed, datetime(2026, 1, 1), ids[::-1])
    predicted, uncertainty = service.predict(speeds, observed, datetime(2026, 1, 1), ids)
    assert predicted.shape == uncertainty.shape == (12, 4)
