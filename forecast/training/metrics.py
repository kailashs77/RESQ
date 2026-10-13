"""Masked traffic-speed metrics in mph."""

from __future__ import annotations

import numpy as np
import torch


def masked_mae(prediction: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    return ((prediction - target).abs() * mask).sum() / mask.sum().clamp_min(1)


def summarize(prediction: np.ndarray, target: np.ndarray, mask: np.ndarray) -> dict:
    if prediction.shape != target.shape or mask.shape != target.shape:
        raise ValueError("prediction, target and mask must have the same shape")
    if prediction.ndim != 3 or prediction.shape[1] != 12:
        raise ValueError("expected [windows, 12 horizons, nodes]")
    result: dict = {"horizons": {}}
    for step in (1, 3, 6, 12):
        valid = mask[:, step - 1].astype(bool)
        errors = prediction[:, step - 1][valid] - target[:, step - 1][valid]
        truths = target[:, step - 1][valid]
        if not len(errors):
            raise ValueError("no observed targets for a horizon")
        result["horizons"][str(step * 5)] = {
            "mae_mph": float(np.mean(np.abs(errors))),
            "rmse_mph": float(np.sqrt(np.mean(errors**2))),
            "mape_percent": float(100 * np.mean(np.abs(errors) / np.maximum(truths, 1.0))),
            "count": int(len(errors)),
        }
    valid = mask.astype(bool)
    result["all_horizons_mae_mph"] = float(np.mean(np.abs(prediction[valid] - target[valid])))
    return result
