"""Deterministic synthetic ground-truth speeds, standing in for live sensor
history until the macro/perception adapters are wired into the brain.

Each edge gets a reproducible congestion cycle (seeded from its edge id, not
from process-global randomness) so forecasts are stable across restarts but
still vary edge to edge and over time — enough signal for the historical and
spatial-temporal forecasters to disagree occasionally, which is the case the
fusion layer exists to handle.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi, sin
from random import Random
from zlib import crc32

WEEK_S = 7 * 24 * 60 * 60


def _edge_seed(edge_id: str) -> int:
    return crc32(edge_id.encode())


@dataclass(frozen=True, slots=True)
class EdgeProfile:
    edge_id: str
    base_speed_mps: float
    amplitude: float
    period_s: float
    phase: float
    congestion_prone: bool

    def baseline_speed_at(self, t_s: float) -> float:
        """Mild, always-on fluctuation — what the historical model is trained on."""
        cycle = sin(2 * pi * (t_s % self.period_s) / self.period_s + self.phase)
        speed = self.base_speed_mps * (1 + self.amplitude * cycle)
        noise_rng = Random((_edge_seed(self.edge_id) ^ int(t_s // 5)) & 0xFFFFFFFF)
        noise = noise_rng.uniform(-0.06, 0.06) * self.base_speed_mps
        return max(0.8, speed + noise)

    def live_speed_at(self, t_s: float, scenario_active: bool) -> float:
        """Current ground truth, including a live congestion scenario the
        historical model (trained ahead of time) has no way to know about."""
        if self.congestion_prone and scenario_active:
            noise_rng = Random((_edge_seed(self.edge_id) ^ int(t_s // 5)) & 0xFFFFFFFF)
            noise = noise_rng.uniform(-0.02, 0.02) * self.base_speed_mps
            return max(0.5, self.base_speed_mps * 0.08 + noise)
        return self.baseline_speed_at(t_s)


class SyntheticTrafficSource:
    """Ground-truth speed model used to seed both forecasters and to sample
    "recent" observations, in lieu of a live probe/macro feed."""

    def __init__(
        self, base_speeds_mps: dict[str, float], congested_edges: set[str] = frozenset()
    ) -> None:
        self._profiles: dict[str, EdgeProfile] = {}
        for edge_id, base_speed in base_speeds_mps.items():
            seed = _edge_seed(edge_id)
            self._profiles[edge_id] = EdgeProfile(
                edge_id=edge_id,
                base_speed_mps=base_speed,
                amplitude=0.15 + (seed % 100) / 1000,
                period_s=180.0 + (seed % 90),
                phase=(seed % 1000) / 1000 * 2 * pi,
                congestion_prone=edge_id in congested_edges,
            )

    def speed_at(self, edge_id: str, t_s: float) -> float:
        """Baseline ground truth (what the historical model is fit on)."""
        return self._profiles[edge_id].baseline_speed_at(t_s)

    def recent_speeds(
        self,
        edge_id: str,
        now_s: float,
        scenario_active: bool = False,
        samples: int = 3,
        step_s: float = 5.0,
    ) -> list[float]:
        profile = self._profiles[edge_id]
        return [
            profile.live_speed_at(now_s - (samples - 1 - index) * step_s, scenario_active)
            for index in range(samples)
        ]

    def week_history(self, edge_id: str, bucket_minutes: int = 60, samples_per_bucket: int = 3):
        from forecast import SpeedRecord

        records: list[SpeedRecord] = []
        rng = Random(_edge_seed(edge_id))
        for minute in range(0, 7 * 24 * 60, bucket_minutes):
            for _ in range(samples_per_bucket):
                jittered_minute = minute + rng.uniform(0, bucket_minutes)
                speed = self.speed_at(edge_id, jittered_minute * 60)
                records.append(SpeedRecord(edge_id, int(jittered_minute), speed))
        return records
