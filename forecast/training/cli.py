"""Prepare, train and evaluate a reproducible Graph WaveNet checkpoint."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

from .calibration import calibrate, coverage_90
from .data import DATASETS, INPUT_STEPS, TrafficData, prepare, sha256, split_bounds
from .graph_wavenet import GraphWaveNet
from .metrics import masked_mae, summarize

ROOT = Path(__file__).resolve().parents[2]
# The two public benchmarks are downloaded; CHENNAI-SIM is produced by
# sim/chennai/forecast_dataset.py from repeatable SUMO runs of the study area.
TRAINABLE = (*DATASETS, "CHENNAI-SIM")


def _paths(dataset: str) -> tuple[Path, Path]:
    stem = dataset.lower().replace("-", "_")
    return ROOT / "data" / "processed" / f"{stem}.npz", ROOT / "data" / "processed" / f"{stem}.json"


def _loader(data: TrafficData, split: str, batch: int, shuffle: bool = False) -> DataLoader:
    return DataLoader(data.windows(split), batch_size=batch, shuffle=shuffle, num_workers=0)


def _model(data: TrafficData, device: torch.device) -> GraphWaveNet:
    return GraphWaveNet(torch.from_numpy(data.adjacency)).to(device)


def _evaluate_loader(
    model: GraphWaveNet, loader: DataLoader, data: TrafficData, device: torch.device
) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    model.eval()
    outputs, targets, masks = [], [], []
    with torch.inference_mode():
        for x, y, mask in loader:
            estimate = model(x.to(device)) * data.std + data.mean
            outputs.append(estimate.cpu().numpy())
            targets.append(y.numpy())
            masks.append(mask.numpy())
    prediction = np.concatenate(outputs)
    target = np.concatenate(targets)
    mask = np.concatenate(masks)
    return summarize(prediction, target, mask), prediction, target, mask


def _baseline(data: TrafficData, split: str) -> dict:
    start, end = split_bounds(len(data.speed))["train"]
    dates = pd.DatetimeIndex(data.timestamps)
    slots = np.asarray(dates.dayofweek * 288 + dates.hour * 12 + dates.minute // 5)
    train_slots = slots[start:end]
    n = len(data.ids)
    sums = np.zeros((2016, n), dtype=np.float64)
    counts = np.zeros((2016, n), dtype=np.float64)
    np.add.at(sums, train_slots, np.where(data.observed[start:end], data.speed[start:end], 0))
    np.add.at(counts, train_slots, data.observed[start:end].astype(np.float64))
    node_sums = sums.sum(axis=0)
    node_counts = counts.sum(axis=0)
    node_mean = node_sums / np.maximum(node_counts, 1)
    weekly = np.where(counts > 0, sums / np.maximum(counts, 1), node_mean[None, :])
    starts = data.windows(split).starts
    target_positions = starts[:, None] + INPUT_STEPS + np.arange(12)[None, :]
    targets = data.speed[target_positions]
    masks = data.observed[target_positions]
    persistence = np.broadcast_to(data.filled[starts + INPUT_STEPS - 1, None, :], targets.shape)
    historical = weekly[slots[target_positions]]
    return {
        "persistence": summarize(persistence, targets, masks),
        "historical_average": summarize(historical, targets, masks),
    }


def _checkpoint_paths(dataset: str, seed: int) -> tuple[Path, Path]:
    folder = ROOT / "artifacts" / "forecast" / dataset.lower().replace("-", "_")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"graph_wavenet_seed{seed}.pt", folder / f"graph_wavenet_seed{seed}.json"


def train(args: argparse.Namespace) -> None:
    processed, manifest_path = _paths(args.dataset)
    if not processed.exists():
        raise FileNotFoundError(f"prepare {args.dataset} first")
    data = TrafficData.load(processed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    model = _model(data, device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-5
    )
    amp = device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=amp)
    train_loader = _loader(data, "train", args.batch, shuffle=True)
    val_loader = _loader(data, "val_select", args.batch)
    weights_path, metadata_path = _checkpoint_paths(args.dataset, args.seed)
    best = float("inf")
    stalled = 0
    start_time = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss, batches = 0.0, 0
        for x, y, mask in train_loader:
            x, y, mask = x.to(device), y.to(device), mask.to(device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, enabled=amp):
                estimate = model(x) * data.std + data.mean
                loss = masked_mae(estimate, y, mask)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            scaler.step(optimizer)
            scaler.update()
            total_loss += float(loss.detach())
            batches += 1
        val_metrics, _, _, _ = _evaluate_loader(model, val_loader, data, device)
        val_mae = val_metrics["all_horizons_mae_mph"]
        scheduler.step(val_mae)
        print(
            f"{args.dataset} seed={args.seed} epoch={epoch} "
            f"train_mae={total_loss / batches:.4f} val_mae={val_mae:.4f}",
            flush=True,
        )
        if val_mae < best - 1e-4:
            best, stalled = val_mae, 0
            torch.save(model.state_dict(), weights_path)
            try:
                revision = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip()
            except (OSError, subprocess.CalledProcessError):
                revision = "unknown"
            metadata = {
                "model": "GraphWaveNet",
                "dataset": args.dataset,
                "seed": args.seed,
                "best_epoch": epoch,
                "best_validation_mae_mph": best,
                "batch_size": args.batch,
                "learning_rate": args.lr,
                "input_steps": 12,
                "output_steps": 12,
                "speed_unit": "mph",
                "selection_split": "first chronological half of validation",
                "calibration_split": "second chronological half of validation",
                "sensor_ids": data.ids,
                "data_manifest": json.loads(manifest_path.read_text()),
                "git_revision": revision,
                "torch_version": torch.__version__,
                "code_sha256": {
                    file_name: sha256(Path(__file__).with_name(file_name))
                    for file_name in ("data.py", "graph_wavenet.py", "calibration.py")
                },
                "device": str(device),
                "elapsed_seconds": round(time.time() - start_time, 1),
            }
            metadata_path.write_text(json.dumps(metadata, indent=2))
        else:
            stalled += 1
        if stalled >= args.patience:
            print(f"early stopping after epoch {epoch}", flush=True)
            break
    model.load_state_dict(torch.load(weights_path, map_location=device, weights_only=True))
    _, cal_prediction, cal_target, cal_mask = _evaluate_loader(
        model, _loader(data, "calibration", args.batch), data, device
    )
    calibration = calibrate(cal_prediction, cal_target, cal_mask)
    calibration_path = metadata_path.with_name(metadata_path.stem + "_calibration.json")
    calibration_path.write_text(json.dumps(calibration))
    test_metrics, test_prediction, test_target, test_mask = _evaluate_loader(
        model, _loader(data, "test", args.batch), data, device
    )
    test_metrics["coverage_90"] = coverage_90(
        test_prediction,
        test_target,
        test_mask,
        np.asarray(calibration["q90_mph"], dtype=np.float32),
    )
    baselines = _baseline(data, "test")
    report = {
        "dataset": args.dataset,
        "seed": args.seed,
        "model": test_metrics,
        "baselines": baselines,
        "checkpoint": str(weights_path),
    }
    report_path = metadata_path.with_name(metadata_path.stem + "_test.json")
    report_path.write_text(json.dumps(report, indent=2))
    print(f"test report: {report_path}", flush=True)
    print(json.dumps(test_metrics, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("dataset", choices=DATASETS)
    fitting = commands.add_parser("train")
    fitting.add_argument("dataset", choices=TRAINABLE)
    fitting.add_argument("--seed", type=int, default=42)
    fitting.add_argument("--batch", type=int, default=32)
    fitting.add_argument("--epochs", type=int, default=100)
    fitting.add_argument("--patience", type=int, default=15)
    fitting.add_argument("--lr", type=float, default=1e-3)
    fitting.add_argument("--cpu", action="store_true")
    baseline = commands.add_parser("baseline")
    baseline.add_argument("dataset", choices=TRAINABLE)
    args = parser.parse_args()
    if args.command == "prepare":
        path = prepare(args.dataset, ROOT / "data" / "raw", ROOT / "data" / "processed")
        print(f"prepared: {path}")
    elif args.command == "train":
        train(args)
    else:
        data = TrafficData.load(_paths(args.dataset)[0])
        baselines = _baseline(data, "test")
        folder = ROOT / "artifacts" / "forecast" / args.dataset.lower().replace("-", "_")
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / "baselines_test.json"
        output.write_text(json.dumps(baselines, indent=2))
        print(f"baseline report: {output}")
        print(json.dumps(baselines, indent=2))


if __name__ == "__main__":
    main()
