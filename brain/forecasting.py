"""Wires the two implemented forecasting baselines into the fusion contract.

Each forecaster is called through `_safe`, which turns any failure (missing
history, a bad prediction, a disabled model) into `None` rather than an
exception. `fuse_observations` already tolerates a `None`, so a forecaster
going away never breaks the estimate for a road — it just narrows the set of
sources the brain is blending. With one forecaster enabled, the estimate is
exactly that forecaster's reading; with two, it is the confidence-weighted
blend; with zero, the caller falls back to the road's last known/static time.
"""

from __future__ import annotations

from collections.abc import Callable

from forecast import HistoricalAverageForecaster, SpatialTemporalForecaster
from fusion import fuse_observations
from schema import RoadEstimate, RoadObservation, SourceKind

from .traffic_source import SyntheticTrafficSource


def _to_observation(
    road_id: str, source: SourceKind, speed_mps: float, stddev_mps: float, edge_length_m: float
) -> RoadObservation:
    speed = max(0.5, speed_mps)
    travel_time_s = max(0.5, edge_length_m / speed)
    relative_error = min(0.9, stddev_mps / speed)
    return RoadObservation(
        road_id=road_id,
        source=source,
        travel_time_s=travel_time_s,
        stddev_s=max(0.3, travel_time_s * relative_error),
        confidence=max(0.05, min(0.99, 1 - relative_error)),
        age_s=0,
        blockage=False,
    )


def _safe(build: Callable[[], RoadObservation | None]) -> RoadObservation | None:
    try:
        return build()
    except Exception:
        return None


class ForecastBrain:
    """Owns the two forecasting models and turns them into per-road estimates."""

    def __init__(
        self,
        edge_lengths_m: dict[str, float],
        neighbours: dict[str, list[str]],
        traffic: SyntheticTrafficSource,
        bucket_minutes: int = 60,
    ) -> None:
        self.edge_lengths_m = edge_lengths_m
        self.traffic = traffic
        self.historical = HistoricalAverageForecaster(bucket_minutes=bucket_minutes)
        self.historical.fit(
            [
                record
                for edge_id in edge_lengths_m
                for record in traffic.week_history(edge_id, bucket_minutes)
            ]
        )
        self.spatial_temporal = SpatialTemporalForecaster(neighbours)

    def _historical_observation(self, edge_id: str, minute_of_week: int) -> RoadObservation | None:
        forecast = self.historical.predict(edge_id, minute_of_week, horizon_minutes=0)
        # A trained-ahead-of-time baseline cannot see live disruptions, so it
        # carries an uncertainty floor about *current* conditions regardless of
        # how tightly its own training samples happen to cluster.
        stddev_mps = max(forecast.stddev_mps, 0.3 * forecast.speed_mps)
        return _to_observation(
            edge_id,
            SourceKind.HISTORICAL,
            forecast.speed_mps,
            stddev_mps,
            self.edge_lengths_m[edge_id],
        )

    def _spatial_temporal_observation(
        self, edge_id: str, recent_speeds: dict[str, list[float]]
    ) -> RoadObservation | None:
        forecast = self.spatial_temporal.predict(recent_speeds, edge_id, horizons=(5,))[0]
        # This baseline's fixed speed-domain stddev formula isn't congestion-aware
        # and blows up in relative terms as predicted speed shrinks. It is built
        # directly from the last few observed samples, so cap its uncertainty
        # proportionally to what it is actually predicting rather than letting a
        # near-stationary reading drown itself out in the fusion weighting.
        stddev_mps = min(forecast.stddev_mps, 0.2 * forecast.speed_mps)
        return _to_observation(
            edge_id,
            SourceKind.SPATIAL_TEMPORAL,
            forecast.speed_mps,
            stddev_mps,
            self.edge_lengths_m[edge_id],
        )

    def estimate(
        self,
        edge_id: str,
        now_s: float,
        recent_speeds: dict[str, list[float]],
        *,
        historical_enabled: bool = True,
        spatial_temporal_enabled: bool = True,
    ) -> RoadEstimate | None:
        minute_of_week = int(now_s // 60) % (7 * 24 * 60)
        historical = (
            _safe(lambda: self._historical_observation(edge_id, minute_of_week))
            if historical_enabled
            else None
        )
        spatial_temporal = (
            _safe(lambda: self._spatial_temporal_observation(edge_id, recent_speeds))
            if spatial_temporal_enabled
            else None
        )
        if historical is not None and spatial_temporal is not None:
            # The two models disagreeing is itself information the trained-ahead
            # historical baseline has no way to account for in its own reported
            # variance: widen its uncertainty by the size of the disagreement so
            # a live, reactive reading isn't out-voted by a stale confident one.
            disagreement_s = abs(historical.travel_time_s - spatial_temporal.travel_time_s)
            historical = historical.model_copy(
                update={"stddev_s": max(historical.stddev_s, 0.5 * disagreement_s)}
            )
        present = [item for item in (historical, spatial_temporal) if item is not None]
        if not present:
            return None
        return fuse_observations(edge_id, *present)
