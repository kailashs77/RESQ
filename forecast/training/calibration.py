"""Calibration from a held-out, chronological portion of validation data."""

from __future__ import annotations

import numpy as np


def calibrate(
    prediction: np.ndarray, target: np.ndarray, mask: np.ndarray
) -> dict[str, list[list[float]]]:
    if prediction.shape != target.shape or mask.shape != target.shape:
        raise ValueError("prediction, target and mask must have matching shapes")
    residual = prediction - target
    absolute = np.abs(residual)
    horizon_count, node_count = residual.shape[1:]
    standard = np.zeros((horizon_count, node_count), dtype=np.float32)
    quantile = np.zeros_like(standard)
    for horizon in range(horizon_count):
        global_errors = residual[:, horizon][mask[:, horizon].astype(bool)]
        global_std = float(np.std(global_errors))
        global_q = float(np.quantile(np.abs(global_errors), 0.9))
        for node in range(node_count):
            valid = mask[:, horizon, node].astype(bool)
            errors = residual[:, horizon, node][valid]
            if len(errors) < 100:
                standard[horizon, node] = max(global_std, 0.1)
                quantile[horizon, node] = max(global_q, 0.1)
                continue
            # The finite-sample conformal rank gives marginal coverage on future
            # exchangeable errors; temporal drift is checked on the test split.
            rank = min(1.0, np.ceil(0.9 * (len(errors) + 1)) / len(errors))
            standard[horizon, node] = max(float(np.std(errors)), 0.1)
            quantile[horizon, node] = max(
                float(np.quantile(absolute[:, horizon, node][valid], rank, method="higher")),
                0.1,
            )
    return {"stddev_mph": standard.tolist(), "q90_mph": quantile.tolist()}


def coverage_90(
    prediction: np.ndarray, target: np.ndarray, mask: np.ndarray, quantile: np.ndarray
) -> float:
    if quantile.shape != prediction.shape[1:]:
        raise ValueError("quantiles must be [horizons, nodes]")
    covered = np.abs(prediction - target) <= quantile[None, :, :]
    valid = mask.astype(bool)
    return float(covered[valid].mean())
