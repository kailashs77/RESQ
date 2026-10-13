"""Run the ResQ Dijkstra ambulance trip through the generated Chennai network."""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import sumolib
import traci
from sumolib.geomhelper import distancePointToPolygon

from sim.chennai.roads import HOSPITAL, ORIGIN

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "chennai.sumocfg"
NETWORK = HERE / "generated" / "thousand_lights.net.xml"
ROUTE_FILE = HERE / "generated" / "ambulance.rou.xml"
AMBULANCE_ID = "resq_ambulance"


def _binary(gui: bool) -> Path:
    name = "sumo-gui" if gui else "sumo"
    if os.name == "nt":
        name += ".exe"
    candidates = [
        Path(sys.prefix) / "Scripts" / name,
        Path(os.getenv("SUMO_HOME", "")) / "bin" / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise SystemExit(f"{name} is unavailable; install eclipse-sumo or set SUMO_HOME")


def _planned_route(network: sumolib.net.Net) -> tuple[list[object], float, float]:
    """Load the Dijkstra route written by build_ambulance_route.py."""

    vehicle = ET.parse(ROUTE_FILE).getroot().find(f"vehicle[@id='{AMBULANCE_ID}']")
    route = vehicle.find("route") if vehicle is not None else None
    if route is None:
        raise SystemExit(f"{AMBULANCE_ID} is missing from {ROUTE_FILE}; rebuild the network")
    edges = [network.getEdge(edge_id) for edge_id in route.get("edges", "").split()]
    origin_xy = traci.simulation.convertGeo(ORIGIN.lon, ORIGIN.lat, fromGeo=True)
    hospital_xy = traci.simulation.convertGeo(HOSPITAL.lon, HOSPITAL.lat, fromGeo=True)
    start_snap_m = distancePointToPolygon(origin_xy, edges[0].getShape())
    finish_snap_m = distancePointToPolygon(hospital_xy, edges[-1].getShape())
    return edges, start_snap_m, finish_snap_m


def _spotlight(connection: traci.connection.Connection) -> None:
    """Make the ambulance easy to find in sumo-gui.

    Selecting it shows its name label and, via ``vehicle_constantSizeSelected``
    in review.view.xml, keeps only the ambulance readable when zoomed out while
    every other vehicle stays to scale. The highlight draws a ring around it.
    Background traffic (randomTrips' uncoloured "passenger" type) is muted so the
    red ambulance stands out.
    """

    connection.vehicletype.setColor("passenger", (84, 104, 124, 255))
    connection.gui.toggleSelection(AMBULANCE_ID)
    connection.vehicle.highlight(AMBULANCE_ID, color=(255, 30, 30, 255), size=30)


def run(*, gui: bool = False, delay_ms: int = 50, end_s: float = 900) -> dict[str, object]:
    if not CONFIG.exists() or not NETWORK.exists() or not ROUTE_FILE.exists():
        raise SystemExit("Generate the network first: python sim/chennai/build_network.py")
    command = [str(_binary(gui)), "-c", str(CONFIG), "--start", "--no-step-log", "true"]
    if gui:
        # No --quit-on-end: the window must stay open for the demo. The config's
        # end time keeps background traffic running after the ambulance arrives.
        command.extend(["--delay", str(max(0, delay_ms))])
    else:
        command.extend(["--quit-on-end", "--end", str(end_s)])
    traci.start(command, label="resq-sumo")
    connection = traci.getConnection("resq-sumo")
    try:
        network = sumolib.net.readNet(str(NETWORK))
        route_edges, start_snap_m, finish_snap_m = _planned_route(network)
        started_at: float | None = None
        arrived = False
        max_speed = 0.0
        halting_steps = 0
        signal_encounters: set[str] = set()
        total_departed = 0
        total_arrived = 0
        total_collisions = 0
        while connection.simulation.getTime() < end_s:
            connection.simulationStep()
            total_departed += connection.simulation.getDepartedNumber()
            total_arrived += connection.simulation.getArrivedNumber()
            total_collisions += connection.simulation.getCollidingVehiclesNumber()
            ids = set(connection.vehicle.getIDList())
            if AMBULANCE_ID in ids:
                if started_at is None:
                    started_at = connection.vehicle.getDeparture(AMBULANCE_ID)
                    connection.vehicle.setSpeedMode(AMBULANCE_ID, 7)
                    if gui:
                        _spotlight(connection)
                speed = connection.vehicle.getSpeed(AMBULANCE_ID)
                max_speed = max(max_speed, speed)
                halting_steps += int(speed < 0.1)
                upcoming = connection.vehicle.getNextTLS(AMBULANCE_ID)
                signal_encounters.update(item[0] for item in upcoming)
            elif AMBULANCE_ID in connection.simulation.getArrivedIDList():
                arrived = True
                break
        finished_at = connection.simulation.getTime()
        result = {
            "arrived": arrived,
            "vehicle_id": AMBULANCE_ID,
            "travel_time_s": round(finished_at - (started_at or 0.0), 1),
            "route_edges": len(route_edges),
            "route_length_m": round(sum(edge.getLength() for edge in route_edges), 1),
            "estimated_freeflow_s": round(
                sum(edge.getLength() / edge.getSpeed() for edge in route_edges), 1
            ),
            "origin_snap_m": round(start_snap_m, 1),
            "hospital_snap_m": round(finish_snap_m, 1),
            "max_speed_mps": round(max_speed, 1),
            "halting_time_s": round(halting_steps * 0.2, 1),
            "signal_encounters": len(signal_encounters),
            "background_departed": max(0, total_departed - 1),
            "all_vehicles_arrived": total_arrived,
            "collision_events": total_collisions,
        }
        print(json.dumps(result, indent=2), flush=True)
        if gui:
            _keep_running(connection)
        return result
    finally:
        try:
            connection.close()
        except (traci.exceptions.FatalTraCIError, OSError):
            pass  # The SUMO window was already closed.


def _keep_running(connection: traci.connection.Connection) -> None:
    """Keep the GUI simulation alive after the ambulance arrives.

    Closing TraCI ends the simulation (sumo-gui then only offers to close), so
    keep stepping until the config's end time. Play/pause/step and the delay
    slider in sumo-gui keep working, and closing the window ends the loop.
    """

    print(
        "SUMO stays open for the demo: use play/pause/step in the toolbar. "
        "Close the SUMO window to finish.",
        flush=True,
    )
    end_time = connection.simulation.getEndTime()
    try:
        while end_time < 0 or connection.simulation.getTime() < end_time:
            connection.simulationStep()
    except traci.exceptions.FatalTraCIError:
        pass  # Window closed by the presenter.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true", help="Run in the interactive SUMO GUI")
    parser.add_argument("--delay-ms", type=int, default=50, help="GUI delay per simulation step")
    parser.add_argument(
        "--end", type=float, default=900, help="Maximum simulated seconds to wait for the ambulance"
    )
    args = parser.parse_args()
    run(gui=args.gui, delay_ms=args.delay_ms, end_s=args.end)


if __name__ == "__main__":
    main()
