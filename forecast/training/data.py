"""Chronological benchmark data preparation with explicit missing-value masks."""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from torch.utils.data import Dataset

DATASETS = {
    "METR-LA": ("METR-LA.csv", "adj_mx.pkl", 207, 34_272),
    "PEMS-BAY": ("PEMS-BAY.csv", "adj_mx_bay.pkl", 325, 52_116),
}
INPUT_STEPS = 12
OUTPUT_STEPS = 12


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_bounds(length: int) -> dict[str, tuple[int, int]]:
    train_end = int(length * 0.7)
    val_end = int(length * 0.8)
    return {"train": (0, train_end), "val": (train_end, val_end), "test": (val_end, length)}


def _load_adjacency(path: Path, ids: list[str]) -> np.ndarray:
    # These exact public research artifacts are downloaded by the user from the recorded URLs.
    # Never load an arbitrary user-supplied pickle at inference time.
    with path.open("rb") as stream:
        raw_ids, _, matrix = pickle.load(stream, encoding="latin1")  # noqa: S301
    raw_ids = [str(item) for item in raw_ids]
    if set(raw_ids) != set(ids):
        raise ValueError("adjacency sensor IDs do not match speed columns")
    indices = [raw_ids.index(item) for item in ids]
    result = np.asarray(matrix, dtype=np.float32)[np.ix_(indices, indices)]
    if result.shape != (len(ids), len(ids)):
        raise ValueError("invalid adjacency dimensions")
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    result = np.maximum(result, 0.0)
    np.fill_diagonal(result, 1.0)
    return result


def prepare(dataset: str, raw_dir: Path, processed_dir: Path) -> Path:
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset!r}")
    csv_name, graph_name, expected_nodes, expected_rows = DATASETS[dataset]
    csv_path, graph_path = raw_dir / csv_name, raw_dir / graph_name
    if not csv_path.is_file() or not graph_path.is_file():
        raise FileNotFoundError(f"download {csv_name} and {graph_name} into {raw_dir}")
    frame = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    if frame.shape != (expected_rows, expected_nodes):
        raise ValueError(f"unexpected {dataset} dimensions: {frame.shape}")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and ascending")
    timestamps_ns = frame.index.as_unit("ns").asi8
    intervals = np.diff(timestamps_ns)
    valid_intervals = intervals == 5 * 60 * 1_000_000_000
    if not np.all(valid_intervals | (intervals == 65 * 60 * 1_000_000_000)):
        raise ValueError("unexpected timestamp gap; inspect the source dataset")
    ids = [str(value) for value in frame.columns]
    values = frame.to_numpy(dtype=np.float32)
    observed = np.isfinite(values) & (values > 0)
    bounds = split_bounds(len(values))
    train_start, train_end = bounds["train"]
    train = values[train_start:train_end]
    train_mask = observed[train_start:train_end]
    mean = float(train[train_mask].mean())
    std = float(train[train_mask].std())
    if std <= 0:
        raise ValueError("training speed standard deviation is zero")
    # Past-only imputation happens separately within each split. Leading gaps use the
    # training mean, so validation/test statistics cannot leak into the model input.
    filled = np.empty_like(values)
    for start, end in bounds.values():
        part = pd.DataFrame(values[start:end]).where(observed[start:end])
        filled[start:end] = part.ffill().fillna(mean).to_numpy(dtype=np.float32)
    adjacency = _load_adjacency(graph_path, ids)
    processed_dir.mkdir(parents=True, exist_ok=True)
    stem = dataset.lower().replace("-", "_")
    output = processed_dir / f"{stem}.npz"
    np.savez_compressed(
        output,
        speed=values,
        filled=filled,
        observed=observed,
        timestamps=timestamps_ns,
        ids=np.asarray(ids),
        adjacency=adjacency,
        mean=np.float32(mean),
        std=np.float32(std),
    )
    manifest = {
        "dataset": dataset,
        "source": "https://zenodo.org/records/5146275",
        "graph_source": (
            "https://github.com/liyaguang/DCRNN/tree/master/data/sensor_graph"
            if dataset == "METR-LA"
            else "https://zenodo.org/records/5146275"
        ),
        "csv_sha256": sha256(csv_path),
        "graph_sha256": sha256(graph_path),
        "speed_unit": "mph",
        "shape": list(values.shape),
        "split_bounds": bounds,
        "train_mean": mean,
        "train_std": std,
        "daylight_saving_gaps": int((~valid_intervals).sum()),
    }
    (processed_dir / f"{stem}.json").write_text(json.dumps(manifest, indent=2))
    return output


@dataclass(slots=True)
class TrafficData:
    speed: np.ndarray
    filled: np.ndarray
    observed: np.ndarray
    timestamps: np.ndarray
    ids: list[str]
    adjacency: np.ndarray
    mean: float
    std: float

    @classmethod
    def load(cls, path: Path) -> TrafficData:
        with np.load(path, allow_pickle=False) as data:
            return cls(
                speed=data["speed"],
                filled=data["filled"],
                observed=data["observed"],
                timestamps=data["timestamps"],
                ids=data["ids"].tolist(),
                adjacency=data["adjacency"],
                mean=float(data["mean"]),
                std=float(data["std"]),
            )

    def windows(self, split: str) -> TrafficWindows:
        bounds = split_bounds(len(self.speed))
        if split == "val_select":
            start, val_end = bounds["val"]
            end = (start + val_end) // 2
        elif split == "calibration":
            val_start, end = bounds["val"]
            start = (val_start + end) // 2
        else:
            start, end = bounds[split]
        return TrafficWindows(self, start, end)


class TrafficWindows(Dataset):
    def __init__(self, data: TrafficData, start: int, end: int) -> None:
        self.data = data
        self.start = start
        self.end = end
        if end - start < INPUT_STEPS + OUTPUT_STEPS:
            raise ValueError("split is too short to form a complete window")
        candidates = np.arange(start, end - INPUT_STEPS - OUTPUT_STEPS + 1)
        bad = np.diff(data.timestamps) != 5 * 60 * 1_000_000_000
        prefix = np.concatenate(([0], np.cumsum(bad)))
        self.starts = candidates[
            prefix[candidates + INPUT_STEPS + OUTPUT_STEPS - 1] == prefix[candidates]
        ]
        dates = pd.DatetimeIndex(data.timestamps)
        self.tod = ((dates.hour * 60 + dates.minute) / 1440.0).to_numpy(dtype=np.float32)
        self.dow = (dates.dayofweek / 6.0).to_numpy(dtype=np.float32)

    def __len__(self) -> int:
        return len(self.starts)

    def __getitem__(self, index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if index < 0 or index >= len(self):
            raise IndexError(index)
        pos = int(self.starts[index])
        sl = slice(pos, pos + INPUT_STEPS)
        n = len(self.data.ids)
        x = np.empty((INPUT_STEPS, n, 4), dtype=np.float32)
        x[:, :, 0] = (self.data.filled[sl] - self.data.mean) / self.data.std
        x[:, :, 1] = self.tod[sl, None]
        x[:, :, 2] = self.dow[sl, None]
        x[:, :, 3] = self.data.observed[sl]
        future = slice(pos + INPUT_STEPS, pos + INPUT_STEPS + OUTPUT_STEPS)
        y = np.where(self.data.observed[future], self.data.speed[future], 0).astype(np.float32)
        mask = self.data.observed[future].astype(np.float32)
        return x, y, mask
