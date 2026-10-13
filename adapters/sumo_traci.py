"""Optional SUMO/TraCI bridge using the same interface as the fast adapter."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .base import AdapterUnavailable


class SumoTraCIAdapter:
    def __init__(self, config_path: str | Path, *, gui: bool = False, label: str = "resq") -> None:
        self.config_path = Path(config_path)
        self.gui = gui
        self.label = label
        self._traci: Any = None

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("traci") is not None and self._binary().is_file()

    def _binary(self) -> Path:
        name = "sumo-gui" if self.gui else "sumo"
        discovered = shutil.which(name)
        if discovered:
            return Path(discovered)
        suffix = ".exe" if os.name == "nt" else ""
        candidates = [Path(sys.prefix) / "Scripts" / f"{name}{suffix}"]
        configured = os.getenv("SUMO_HOME")
        if configured:
            candidates.append(Path(configured) / "bin" / f"{name}{suffix}")
        specification = importlib.util.find_spec("sumo")
        if specification and specification.submodule_search_locations:
            sumo_home = Path(next(iter(specification.submodule_search_locations)))
            candidates.append(sumo_home / "bin" / f"{name}{suffix}")
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])

    def start(self) -> None:
        if not self.available:
            raise AdapterUnavailable("TraCI is unavailable; install SUMO and its Python tools")
        if not self.config_path.exists():
            raise AdapterUnavailable(f"SUMO config not found: {self.config_path}")
        traci = importlib.import_module("traci")
        traci.start(
            [str(self._binary()), "-c", str(self.config_path), "--start"],
            label=self.label,
        )
        self._traci = traci.getConnection(self.label)

    def step(self, target_time_s: float | None = None) -> dict[str, object]:
        if self._traci is None:
            raise AdapterUnavailable("SUMO adapter has not been started")
        self._traci.simulationStep(target_time_s or 0)
        vehicles = [
            {
                "vehicle_id": vehicle_id,
                "road_id": self._traci.vehicle.getRoadID(vehicle_id),
                "position": self._traci.vehicle.getPosition(vehicle_id),
                "speed_mps": self._traci.vehicle.getSpeed(vehicle_id),
                "next_tls": self._traci.vehicle.getNextTLS(vehicle_id),
            }
            for vehicle_id in self._traci.vehicle.getIDList()
        ]
        return {
            "time_s": self._traci.simulation.getTime(),
            "vehicles": vehicles,
            "traffic_lights": list(self._traci.trafficlight.getIDList()),
        }

    def set_signal_phase(self, signal_id: str, phase_index: int) -> None:
        if self._traci is None:
            raise AdapterUnavailable("SUMO adapter has not been started")
        self._traci.trafficlight.setPhase(signal_id, phase_index)

    def close(self) -> None:
        if self._traci is not None:
            self._traci.close()
            self._traci = None
