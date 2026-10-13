"""Serve the trained corridor forecaster to the rest of the ResQ system.

The training pipeline produces a Graph WaveNet checkpoint for one specific sensor
graph. This module turns that checkpoint into something the decision brain can use:
a rolling twelve-step speed history, a multi-horizon forecast with calibrated
uncertainty, and the macro road observations that the fusion engine already accepts.

The deployed corridor checkpoint is trained on simulated Chennai histories, so every
number it produces is a statement about the SUMO study area, not about measured
Chennai traffic. The public-benchmark checkpoints validate the model class instead;
they stay separate because their sensor graphs are Californian.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from forecast.adapter import speed_forecast_to_observation
from schema import RoadObservation

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "forecast"
PROCESSED = ROOT / "data" / "processed"
SEGMENT_GEOMETRY = ROOT / "sim" / "chennai" / "generated" / "forecast" / "segments.geojson"

CORRIDOR_DATASET = "chennai_sim"
INPUT_STEPS = 12
OUTPUT_STEPS = 12
STEP_MINUTES = 5
HORIZONS_MIN = (5, 15, 30, 60)
# One real second of demonstration advances the replay by one simulated minute.
DEFAULT_SECONDS_PER_STEP = 5.0
EDGE_PATTERN = re.compile(r"^(?P<reverse>-?)(?P<way>\d+)(?:#\d+)?$")


@dataclass(frozen=True, slots=True)
class SegmentForecast:
    segment_id: str
    osm_way_id: str
    name: str
    length_m: float
    free_flow_mph: float
    on_route: bool
    observed_mph: float
    speeds_mph: dict[int, float]
    stddev_mph: dict[int, float]
    actual_mph: dict[int, float | None]

    def travel_time_s(self, horizon_minutes: int) -> float:
        speed = max(self.speeds_mph.get(horizon_minutes, self.observed_mph), 1.0)
        return self.length_m / (speed * 0.44704)


@dataclass(frozen=True, slots=True)
class CorridorState:
    at: datetime
    step: int
    horizon_minutes: tuple[int, ...]
    segments: tuple[SegmentForecast, ...]
    model: str
    source: str
    history_mph: tuple[tuple[float, ...], ...] = field(default=())

    def by_way(self) -> dict[str, list[SegmentForecast]]:
        grouped: dict[str, list[SegmentForecast]] = {}
        for segment in self.segments:
            grouped.setdefault(segment.osm_way_id, []).append(segment)
        return grouped

    def observations(self, horizon_minutes: int, age_s: float = 0.0) -> list[RoadObservation]:
        """Macro observations in the contract the fusion engine already consumes."""
        return [
            speed_forecast_to_observation(
                road_id=segment.segment_id,
                length_m=segment.length_m,
                speed_mph=segment.speeds_mph.get(horizon_minutes, segment.observed_mph),
                stddev_mph=segment.stddev_mph.get(horizon_minutes, 2.0),
                age_s=age_s,
            )
            for segment in self.segments
        ]


def way_of(segment_id: str) -> tuple[str, bool]:
    """Split a SUMO edge identifier into its OpenStreetMap way and direction."""
    match = EDGE_PATTERN.match(segment_id)
    if not match:
        return segment_id, False
    return match.group("way"), bool(match.group("reverse"))


_geometry_cache: tuple[float, dict[str, dict]] | None = None


def segment_geometry() -> dict[str, dict]:
    """Monitored-segment shapes, reloaded when the dataset builder rewrites them."""
    global _geometry_cache
    if not SEGMENT_GEOMETRY.is_file():
        return {}
    stamp = SEGMENT_GEOMETRY.stat().st_mtime
    if _geometry_cache is None or _geometry_cache[0] != stamp:
        payload = json.loads(SEGMENT_GEOMETRY.read_text(encoding="utf-8"))
        _geometry_cache = (
            stamp,
            {
                feature["properties"]["segment_id"]: feature
                for feature in payload.get("features", [])
            },
        )
    return _geometry_cache[1]


class CorridorForecaster:
    """Runs the corridor checkpoint over a replayed held-out speed history.

    The demonstration replays the chronologically last 20 % of the simulated
    histories. Those windows were never used to fit or to select the checkpoint, so
    the predictions shown are genuine out-of-sample model output and the actual
    values are known, which is what lets the interface plot forecast against truth.
    """

    def __init__(
        self,
        dataset: str = CORRIDOR_DATASET,
        seed: int = 42,
        seconds_per_step: float = DEFAULT_SECONDS_PER_STEP,
        device: str = "cpu",
    ) -> None:
        import numpy as np
        import torch

        from forecast.training.graph_wavenet import GraphWaveNet

        self._np = np
        self._torch = torch
        folder = ARTIFACTS / dataset
        weights = folder / f"graph_wavenet_seed{seed}.pt"
        metadata_path = folder / f"graph_wavenet_seed{seed}.json"
        calibration_path = folder / f"graph_wavenet_seed{seed}_calibration.json"
        archive = PROCESSED / f"{dataset}.npz"
        for required in (weights, metadata_path, calibration_path, archive):
            if not required.is_file():
                raise FileNotFoundError(f"corridor forecaster needs {required}")

        self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        report_path = folder / f"graph_wavenet_seed{seed}_test.json"
        self.report = (
            json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        )
        if self.metadata["speed_unit"] != "mph":
            raise ValueError("checkpoint speed unit must be mph")

        with np.load(archive, allow_pickle=False) as data:
            self.speed = data["speed"].astype(np.float32)
            self.ids = [str(item) for item in data["ids"].tolist()]
            self.timestamps = data["timestamps"]
            self.length_m = data["length_m"].astype(np.float32)
            self.free_flow_mph = data["free_flow_mph"].astype(np.float32)
            adjacency = data["adjacency"].astype(np.float32)
            self.mean = float(data["mean"])
            self.std = float(data["std"])
        if self.ids != [str(item) for item in self.metadata["sensor_ids"]]:
            raise ValueError("segment order differs from the trained checkpoint")

        self.uncertainty = np.asarray(calibration["stddev_mph"], dtype=np.float32)
        if self.uncertainty.shape != (OUTPUT_STEPS, len(self.ids)):
            raise ValueError("calibration shape does not match the checkpoint")

        self.device = torch.device(device)
        self.model = GraphWaveNet(torch.from_numpy(adjacency)).to(self.device)
        self.model.load_state_dict(
            torch.load(weights, map_location=self.device, weights_only=True), strict=True
        )
        self.model.eval()

        self.replay_start = int(len(self.speed) * 0.8)
        self.replay_end = len(self.speed) - OUTPUT_STEPS
        if self.replay_end - self.replay_start < INPUT_STEPS + 1:
            raise ValueError("the held-out split is too short to replay")
        self.seconds_per_step = max(0.5, float(seconds_per_step))
        self._anchor = datetime.now(UTC)

    @property
    def geometry(self) -> dict[str, dict]:
        """Read on access, so a rebuilt monitored set is picked up without a restart."""
        return segment_geometry()

    # ------------------------------------------------------------------ replay

    def step_for(self, at: datetime | None = None) -> int:
        at = at or datetime.now(UTC)
        elapsed = (at - self._anchor).total_seconds()
        span = self.replay_end - self.replay_start - INPUT_STEPS
        offset = int(elapsed // self.seconds_per_step) % max(span, 1)
        return self.replay_start + INPUT_STEPS + offset

    def _window(self, step: int):
        np = self._np
        history = self.speed[step - INPUT_STEPS : step]
        features = np.empty((INPUT_STEPS, len(self.ids), 4), dtype=np.float32)
        moments = [
            datetime.fromtimestamp(int(value) / 1_000_000_000, tz=UTC)
            for value in self.timestamps[step - INPUT_STEPS : step]
        ]
        features[:, :, 0] = (history - self.mean) / self.std
        features[:, :, 1] = np.asarray(
            [(m.hour * 60 + m.minute) / 1440.0 for m in moments], dtype=np.float32
        )[:, None]
        features[:, :, 2] = np.asarray(
            [m.weekday() / 6.0 for m in moments], dtype=np.float32
        )[:, None]
        features[:, :, 3] = 1.0
        return features, history, moments[-1]

    # ---------------------------------------------------------------- forecast

    def state(self, at: datetime | None = None) -> CorridorState:
        np, torch = self._np, self._torch
        step = self.step_for(at)
        features, history, last_moment = self._window(step)
        with torch.inference_mode():
            tensor = torch.from_numpy(features[None, ...]).to(self.device)
            prediction = self.model(tensor)[0].cpu().numpy() * self.std + self.mean
        prediction = np.clip(prediction, 1.0, self.free_flow_mph[None, :] * 1.15)

        geometry = self.geometry
        segments: list[SegmentForecast] = []
        for position, segment_id in enumerate(self.ids):
            properties = geometry.get(segment_id, {}).get("properties", {})
            way, _ = way_of(segment_id)
            actual: dict[int, float | None] = {}
            for horizon in HORIZONS_MIN:
                index = step + horizon // STEP_MINUTES - 1
                actual[horizon] = (
                    float(self.speed[index, position]) if index < len(self.speed) else None
                )
            segments.append(
                SegmentForecast(
                    segment_id=segment_id,
                    osm_way_id=str(properties.get("osm_way_id", way)),
                    name=str(properties.get("name", "")),
                    length_m=float(self.length_m[position]),
                    free_flow_mph=float(self.free_flow_mph[position]),
                    on_route=bool(properties.get("on_ambulance_route")),
                    observed_mph=float(history[-1, position]),
                    speeds_mph={
                        horizon: float(prediction[horizon // STEP_MINUTES - 1, position])
                        for horizon in HORIZONS_MIN
                    },
                    stddev_mph={
                        horizon: float(self.uncertainty[horizon // STEP_MINUTES - 1, position])
                        for horizon in HORIZONS_MIN
                    },
                    actual_mph=actual,
                )
            )
        return CorridorState(
            at=last_moment,
            step=step,
            horizon_minutes=HORIZONS_MIN,
            segments=tuple(segments),
            model=(
                f"{self.metadata['model']} · {self.metadata['dataset']} "
                f"seed {self.metadata['seed']}"
            ),
            source="replayed held-out simulated corridor history",
            history_mph=tuple(tuple(float(value) for value in row) for row in history),
        )

    def series(self, segment_id: str, at: datetime | None = None, past: int = 36) -> dict:
        """Recent history, the current forecast, and the truth it will be judged against."""
        np = self._np
        if segment_id not in self.ids:
            raise KeyError(segment_id)
        position = self.ids.index(segment_id)
        step = self.step_for(at)
        start = max(self.replay_start, step - past)
        history = [
            {
                "at": datetime.fromtimestamp(
                    int(self.timestamps[index]) / 1_000_000_000, tz=UTC
                ).isoformat(),
                "speed_mph": round(float(self.speed[index, position]), 2),
            }
            for index in range(start, step)
        ]
        features, _, _ = self._window(step)
        torch = self._torch
        with torch.inference_mode():
            tensor = torch.from_numpy(features[None, ...]).to(self.device)
            prediction = self.model(tensor)[0].cpu().numpy() * self.std + self.mean
        prediction = np.clip(prediction, 1.0, self.free_flow_mph[position] * 1.15)
        future = []
        for horizon_step in range(OUTPUT_STEPS):
            index = step + horizon_step
            band = float(self.uncertainty[horizon_step, position])
            value = float(prediction[horizon_step, position])
            future.append(
                {
                    "at": datetime.fromtimestamp(
                        int(self.timestamps[index]) / 1_000_000_000, tz=UTC
                    ).isoformat()
                    if index < len(self.timestamps)
                    else None,
                    "horizon_minutes": (horizon_step + 1) * STEP_MINUTES,
                    "forecast_mph": round(value, 2),
                    "low_mph": round(max(0.0, value - band), 2),
                    "high_mph": round(value + band, 2),
                    "actual_mph": round(float(self.speed[index, position]), 2)
                    if index < len(self.speed)
                    else None,
                }
            )
        properties = self.geometry.get(segment_id, {}).get("properties", {})
        return {
            "segment_id": segment_id,
            "name": properties.get("name", ""),
            "free_flow_mph": round(float(self.free_flow_mph[position]), 2),
            "history": history,
            "forecast": future,
        }

    # ------------------------------------------------------------- description

    def model_card(self) -> dict:
        manifest = self.metadata.get("data_manifest", {})
        return {
            "model": self.metadata["model"],
            "dataset": self.metadata["dataset"],
            "seed": self.metadata["seed"],
            "trained_on": manifest.get("source", "unknown"),
            "provenance": manifest.get("provenance", "unknown"),
            "segments": len(self.ids),
            "input_minutes": INPUT_STEPS * STEP_MINUTES,
            "horizon_minutes": list(HORIZONS_MIN),
            "speed_unit": "mph",
            "best_epoch": self.metadata.get("best_epoch"),
            "best_validation_mae_mph": self.metadata.get("best_validation_mae_mph"),
            "trained_device": self.metadata.get("device"),
            "torch_version": self.metadata.get("torch_version"),
            "git_revision": self.metadata.get("git_revision"),
            "test_report": self.report.get("model", {}).get("horizons", {}),
            "test_baselines": {
                name: value.get("horizons", {})
                for name, value in self.report.get("baselines", {}).items()
            },
            "coverage_90": self.report.get("model", {}).get("coverage_90"),
            "replay_split": "final 20 % of the simulated history, unseen during training",
            "seconds_per_step": self.seconds_per_step,
            "limitation": (
                "Trained on SUMO simulation of the study area. Accuracy statements apply "
                "to the simulator, not to measured Chennai traffic."
            ),
        }


_forecaster: CorridorForecaster | None = None
_last_attempt = 0.0
RETRY_SECONDS = 20.0


def corridor_forecaster() -> CorridorForecaster | None:
    """The process-wide forecaster, or None when the checkpoint is not built yet.

    A long-running API may be started before training finishes, so a failed load is
    retried periodically rather than cached forever.
    """
    global _forecaster, _last_attempt
    if _forecaster is not None:
        return _forecaster
    now = monotonic()
    if _last_attempt and now - _last_attempt < RETRY_SECONDS:
        return None
    _last_attempt = now
    try:
        _forecaster = CorridorForecaster()
    except (FileNotFoundError, ImportError, ValueError, OSError, KeyError):
        _forecaster = None
    return _forecaster


def benchmark_reports() -> list[dict]:
    """Published-benchmark evidence for the model class, read from the test reports."""
    reports = []
    for dataset in ("metr_la", "pems_bay"):
        folder = ARTIFACTS / dataset
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("graph_wavenet_seed*_test.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            reports.append(
                {
                    "dataset": payload.get("dataset"),
                    "seed": payload.get("seed"),
                    "model": payload.get("model", {}).get("horizons", {}),
                    "coverage_90": payload.get("model", {}).get("coverage_90"),
                    "baselines": {
                        name: value.get("horizons", {})
                        for name, value in payload.get("baselines", {}).items()
                    },
                }
            )
    return reports


def horizon_for_eta(eta_s: float) -> int:
    """Pick the forecast horizon closest to when the vehicle actually arrives."""
    minutes = max(0.0, eta_s) / 60.0
    return min(HORIZONS_MIN, key=lambda horizon: abs(horizon - minutes))
