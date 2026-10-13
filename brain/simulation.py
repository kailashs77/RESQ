"""Thread-safe mission wrapper shared by REST and WebSocket clients."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from time import monotonic

from schema import ControlState, DataHealth, NetworkSnapshot, VehicleState

from .engine import DecisionEngine


class BrainSimulation:
    """Illustrative mission driven by fused synthetic forecasting baselines.

    REST handlers and sockets share a single mission. Serialize updates and ticks
    so simultaneous clients cannot move mission time/state backwards.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.controls = ControlState()
            self._engine = DecisionEngine()
            self._started_at = monotonic()

    def update_controls(self, **changes: bool | int | list[str]) -> ControlState:
        with self._lock:
            current = self.controls.model_dump()
            unknown = set(changes) - set(current)
            if unknown:
                raise ValueError(f"unknown controls: {', '.join(sorted(unknown))}")
            current.update(changes)
            updated = ControlState.model_validate(current)
            if set(updated.blocked_edge_ids) - self._engine.network.edge_index.keys():
                raise ValueError("unknown closure edge ids")
            self.controls = updated
            return deepcopy(self.controls)

    def snapshot(self) -> NetworkSnapshot:
        with self._lock:
            sim_time = monotonic() - self._started_at
            output = self._engine.tick(sim_time, self.controls)
            return NetworkSnapshot(
                generated_at=datetime.now(UTC),
                sim_time_s=sim_time,
                scenario="forecast-brain",
                vehicle=VehicleState(
                    vehicle_id="AMB-01",
                    position=output.vehicle_position,
                    speed_mps=output.vehicle_speed_mps,
                    heading_deg=output.vehicle_heading_deg,
                    route_id=output.selected_route_id,
                    progress=output.progress,
                ),
                roads=output.roads,
                routes=output.routes,
                signals=output.signals,
                decisions=output.decisions,
                controls=self.controls,
                health=DataHealth(
                    macro_age_s=0.0,
                    perception_online=False,
                    overall_confidence=output.overall_confidence,
                ),
                incident=self._engine.plan.incident if self.controls.accident else None,
                routing_decision=output.routing_decision,
            )
