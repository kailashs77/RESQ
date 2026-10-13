"""Batch inference from a trained Graph WaveNet checkpoint."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import torch

from .graph_wavenet import GraphWaveNet


class TrainedGraphForecaster:
    def __init__(
        self,
        weights_path: Path,
        metadata_path: Path,
        calibration_path: Path,
        device: str = "cpu",
    ) -> None:
        self.metadata = json.loads(metadata_path.read_text())
        self.calibration = json.loads(calibration_path.read_text())
        self.sensor_ids = [str(item) for item in self.metadata["sensor_ids"]]
        if self.metadata["speed_unit"] != "mph":
            raise ValueError("checkpoint speed unit must be mph")
        if self.metadata["input_steps"] != 12 or self.metadata["output_steps"] != 12:
            raise ValueError("checkpoint window dimensions are unsupported")
        self.mean = float(self.metadata["data_manifest"]["train_mean"])
        self.std = float(self.metadata["data_manifest"]["train_std"])
        self.uncertainty = np.asarray(self.calibration["stddev_mph"], dtype=np.float32)
        if self.uncertainty.shape != (12, len(self.sensor_ids)):
            raise ValueError("calibration shape does not match checkpoint")
        self.device = torch.device(device)
        self.model = GraphWaveNet(torch.eye(len(self.sensor_ids))).to(self.device)
        state = torch.load(weights_path, map_location=self.device, weights_only=True)
        self.model.load_state_dict(state, strict=True)
        self.model.eval()

    def predict(
        self,
        speeds_mph: np.ndarray,
        observed: np.ndarray,
        last_timestamp: datetime,
        sensor_ids: list[str],
    ) -> tuple[np.ndarray, np.ndarray]:
        if sensor_ids != self.sensor_ids:
            raise ValueError("sensor order differs from the trained checkpoint")
        expected = (12, len(self.sensor_ids))
        if speeds_mph.shape != expected or observed.shape != expected:
            raise ValueError(f"speeds and mask must be {expected}")
        if last_timestamp.minute % 5 != 0 or last_timestamp.second != 0:
            raise ValueError("last timestamp must align with a five-minute interval")
        features = np.empty((*expected, 4), dtype=np.float32)
        latest = np.full(len(self.sensor_ids), self.mean, dtype=np.float32)
        for t in range(12):
            valid = observed[t].astype(bool) & np.isfinite(speeds_mph[t]) & (speeds_mph[t] > 0)
            latest = np.where(valid, speeds_mph[t], latest)
            at = last_timestamp - timedelta(minutes=5 * (11 - t))
            features[t, :, 0] = (latest - self.mean) / self.std
            features[t, :, 1] = (at.hour * 60 + at.minute) / 1440.0
            features[t, :, 2] = at.weekday() / 6.0
            features[t, :, 3] = valid
        inputs = torch.from_numpy(features[None, ...]).to(self.device)
        with torch.inference_mode():
            prediction = self.model(inputs)[0] * self.std + self.mean
        return prediction.cpu().numpy(), self.uncertainty.copy()
