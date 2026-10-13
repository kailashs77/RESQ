"""Build a Chennai corridor speed-history dataset from repeatable SUMO runs.

The public METR-LA and PEMS-BAY benchmarks validate the model class on Californian
freeway sensors. They cannot establish Chennai accuracy, and their sensor graphs do
not map onto this study area. This module produces the missing Chennai training
signal: per-segment mean speeds at five-minute resolution over a configurable number
of simulated days, in exactly the array layout the training pipeline already loads.

Every number here comes from the SUMO microsimulation, not from field measurement.
Chennai claims derived from this dataset are claims about the simulator.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from heapq import heappop, heappush
from math import inf
from pathlib import Path

import numpy as np
import sumolib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
WORK = GENERATED / "forecast"
NETWORK = GENERATED / "thousand_lights.net.xml"
AMBULANCE_ROUTE = GENERATED / "ambulance.rou.xml"
PROCESSED = Path(__file__).resolve().parents[2] / "data" / "processed"

MPS_TO_MPH = 2.2369362920544
INTERVAL_S = 300
STEPS_PER_DAY = 24 * 3600 // INTERVAL_S
DATASET_NAME = "CHENNAI-SIM"
STEM = "chennai_sim"
# The first simulated day is a Monday, so the day-of-week feature is meaningful.
EPOCH = datetime(2024, 1, 1, tzinfo=UTC)

# Hourly insertion rates in vehicles per hour, shaped like an Indian metro arterial
# weekday: a sharp morning commute, a broad midday plateau and a longer evening peak.
# These are modelling assumptions chosen to exercise congestion, not measured demand.
WEEKDAY_RATES = (
    260, 180, 140, 140, 220, 520, 1150, 2100, 3400, 3700, 2600, 2200,
    2250, 2250, 2300, 2600, 3100, 3800, 4000, 3500, 2500, 1700, 1000, 520,
)
WEEKEND_SCALE = 0.68


def _sumo_home() -> Path:
    configured = os.getenv("SUMO_HOME")
    if configured:
        return Path(configured).resolve()
    import importlib.util

    specification = importlib.util.find_spec("sumo")
    if specification and specification.submodule_search_locations:
        return Path(next(iter(specification.submodule_search_locations))).resolve()
    raise SystemExit("SUMO was not found. Install the project SUMO extra or set SUMO_HOME.")


def _binary(name: str) -> str:
    discovered = shutil.which(name)
    if discovered:
        return discovered
    suffix = ".exe" if os.name == "nt" else ""
    for candidate in (
        Path(sys.prefix) / "Scripts" / f"{name}{suffix}",
        _sumo_home() / "bin" / f"{name}{suffix}",
    ):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"SUMO binary is missing: {name}")


def _relative(path: Path) -> str:
    """SUMO writes its own option values into an XML header comment, so a path
    containing a double hyphen produces an unparsable file. Project-relative
    paths keep every generated artifact readable."""
    return str(path.relative_to(Path.cwd())) if path.is_absolute() else str(path)


def daily_rates(day_index: int, rng: np.random.Generator) -> list[float]:
    weekday = (EPOCH + timedelta(days=day_index)).weekday()
    scale = WEEKEND_SCALE if weekday >= 5 else 1.0
    # One demand level per day plus independent hourly variation, so the model sees
    # days that are similar in shape but never identical.
    day_factor = float(rng.normal(1.0, 0.09))
    hourly = rng.normal(1.0, 0.07, size=24)
    return [
        max(60.0, rate * scale * day_factor * float(factor))
        for rate, factor in zip(WEEKDAY_RATES, hourly, strict=True)
    ]


# ---------------------------------------------------------------- segment selection


def ambulance_edges() -> list[str]:
    root = ET.parse(AMBULANCE_ROUTE).getroot()
    route = root.find("vehicle/route")
    if route is None:
        raise SystemExit(f"no route in {AMBULANCE_ROUTE}; rebuild the network")
    return route.get("edges", "").split()


def probe_usage(seed: int, hours: tuple[int, ...] = (8, 18)) -> dict[str, float]:
    """Rank edges by how much traffic they actually carry during peak hours."""
    usage: dict[str, float] = {}
    for hour in hours:
        routes = _generate_trips(WORK / f"probe_{hour}.rou.xml", WEEKDAY_RATES[hour], seed + hour)
        output = _run_sumo(routes, WORK / f"probe_{hour}.out.xml", edges=None, duration_s=3600)
        for interval in ET.parse(output).getroot().findall("interval"):
            for edge in interval.findall("edge"):
                sampled = float(edge.get("sampledSeconds", 0.0))
                if sampled > 0:
                    usage[edge.get("id", "")] = usage.get(edge.get("id", ""), 0.0) + sampled
    return usage


def select_segments(count: int, seed: int, min_length_m: float = 40.0) -> list[str]:
    net = sumolib.net.readNet(str(NETWORK))
    usage = probe_usage(seed)
    route = [edge_id for edge_id in ambulance_edges() if net.hasEdge(edge_id)]
    eligible = [
        edge.getID()
        for edge in net.getEdges()
        if edge.allows("passenger")
        and edge.getLength() >= min_length_m
        and usage.get(edge.getID(), 0.0) > 0
    ]
    eligible.sort(key=lambda edge_id: (-usage[edge_id], edge_id))
    selected = list(dict.fromkeys(route + eligible))[:count]
    if len(selected) < count:
        raise SystemExit(f"only {len(selected)} segments carry probe traffic; lower --segments")
    return selected


def _edge_graph(net: sumolib.net.Net) -> dict[str, list[tuple[str, float]]]:
    graph: dict[str, list[tuple[str, float]]] = {}
    for edge in net.getEdges():
        graph[edge.getID()] = [
            (successor.getID(), successor.getLength())
            for successor in edge.getOutgoing()
            if successor.allows("passenger")
        ]
    return graph


def build_adjacency(segments: list[str], threshold: float = 0.1) -> np.ndarray:
    """Thresholded Gaussian kernel over driving distance, as in the DCRNN sensor graph.

    Distance is measured forward along the road network, so the matrix is directed:
    row i holds the influence of segment i on the segments reachable from it.
    """
    net = sumolib.net.readNet(str(NETWORK))
    graph = _edge_graph(net)
    index = {segment: position for position, segment in enumerate(segments)}
    distances = np.full((len(segments), len(segments)), np.inf, dtype=np.float64)
    for source in segments:
        best: dict[str, float] = {source: 0.0}
        queue: list[tuple[float, str]] = [(0.0, source)]
        while queue:
            cost, node = heappop(queue)
            if cost > best.get(node, inf):
                continue
            if node in index:
                distances[index[source], index[node]] = min(
                    distances[index[source], index[node]], cost
                )
            for neighbour, length in graph.get(node, []):
                candidate = cost + length
                if candidate < best.get(neighbour, inf):
                    best[neighbour] = candidate
                    heappush(queue, (candidate, neighbour))
    finite = distances[np.isfinite(distances) & (distances > 0)]
    sigma = float(finite.std()) if finite.size else 1.0
    weights = np.exp(-((distances / max(sigma, 1.0)) ** 2))
    weights[~np.isfinite(distances)] = 0.0
    weights[weights < threshold] = 0.0
    np.fill_diagonal(weights, 1.0)
    return weights.astype(np.float32)


# ------------------------------------------------------------------- simulation


def _generate_trips(destination: Path, rates: float | list[float], seed: int) -> Path:
    values = [rates] if isinstance(rates, int | float) else list(rates)
    duration = 3600 * len(values)
    command = [
        sys.executable,
        str(_sumo_home() / "tools" / "randomTrips.py"),
        "-n", _relative(NETWORK),
        "-o", _relative(destination.with_suffix(".trips.xml")),
        "-r", _relative(destination),
        "-b", "0",
        "-e", str(duration),
        "--insertion-rate", *[f"{value:.1f}" for value in values],
        "--seed", str(seed),
        "--vehicle-class", "passenger",
        "--fringe-factor", "5",
        "--validate",
        "--trip-attributes", 'departLane="best" departSpeed="max"',
    ]
    environment = os.environ | {"SUMO_HOME": str(_sumo_home())}
    subprocess.run(command, check=True, env=environment, capture_output=True)  # noqa: S603
    return destination


def _run_sumo(
    routes: Path, output: Path, edges: list[str] | None, duration_s: int, seed: int = 42
) -> Path:
    additional = output.with_suffix(".add.xml")
    attributes = f' edges="{" ".join(edges)}"' if edges else ""
    additional.write_text(
        "<additional>\n"
        f'  <edgeData id="speed" file="{output.name}" period="{INTERVAL_S}"'
        f' begin="0" end="{duration_s}" excludeEmpty="false" withInternal="false"'
        f"{attributes}/>\n"
        "</additional>\n",
        encoding="utf-8",
    )
    command = [
        _binary("sumo"),
        "-n", _relative(NETWORK),
        "-r", _relative(routes),
        "-a", _relative(additional),
        "--begin", "0",
        "--end", str(duration_s),
        "--step-length", "1",
        "--seed", str(seed),
        "--time-to-teleport", "300",
        "--no-step-log", "true",
        "--no-warnings", "true",
        "--xml-validation", "never",
    ]
    subprocess.run(command, check=True, capture_output=True, cwd=str(Path.cwd()))  # noqa: S603
    # SUMO resolves an edgeData file name against the additional file's directory;
    # older builds resolve it against the working directory instead.
    if not output.exists():
        fallback = Path.cwd() / output.name
        if not fallback.exists():
            raise SystemExit(f"SUMO produced no edge data for {routes.name}")
        shutil.move(str(fallback), str(output))
    return output


def _read_intervals(path: Path, segments: list[str]) -> tuple[np.ndarray, np.ndarray]:
    index = {segment: position for position, segment in enumerate(segments)}
    speed = np.zeros((STEPS_PER_DAY, len(segments)), dtype=np.float32)
    sampled = np.zeros((STEPS_PER_DAY, len(segments)), dtype=bool)
    for interval in ET.parse(path).getroot().findall("interval"):
        step = int(float(interval.get("begin", 0)) // INTERVAL_S)
        if not 0 <= step < STEPS_PER_DAY:
            continue
        for edge in interval.findall("edge"):
            position = index.get(edge.get("id", ""))
            if position is None:
                continue
            value = edge.get("speed")
            if value is not None and float(edge.get("sampledSeconds", 0.0)) > 0:
                speed[step, position] = float(value)
                sampled[step, position] = True
    return speed, sampled


@dataclass(frozen=True, slots=True)
class DayResult:
    index: int
    speed_mps: np.ndarray
    sampled: np.ndarray


def simulate_day(day_index: int, segments: list[str], seed: int) -> DayResult:
    rng = np.random.default_rng(seed + day_index)
    rates = daily_rates(day_index, rng)
    routes = _generate_trips(WORK / f"day{day_index:02d}.rou.xml", rates, seed + day_index)
    output = _run_sumo(
        routes,
        WORK / f"day{day_index:02d}.out.xml",
        segments,
        duration_s=24 * 3600,
        seed=seed + day_index,
    )
    speed, sampled = _read_intervals(output, segments)
    for artefact in (routes, routes.with_suffix(".trips.xml"), output.with_suffix(".add.xml")):
        artefact.unlink(missing_ok=True)
    return DayResult(day_index, speed, sampled)


def _worker(payload: tuple[int, list[str], int, str]) -> tuple[int, np.ndarray, np.ndarray]:
    day_index, segments, seed, cwd = payload
    os.chdir(cwd)
    result = simulate_day(day_index, segments, seed)
    return result.index, result.speed_mps, result.sampled


# -------------------------------------------------------------------- assembly


def _write_segment_geometry(
    net: sumolib.net.Net, segments: list[str], free_flow: np.ndarray, lengths: np.ndarray
) -> Path:
    """Publish the monitored segments so the map and the router can use them.

    A SUMO edge imported from OpenStreetMap keeps the OSM way identifier, optionally
    with a '#part' suffix and a '-' prefix for the reverse direction. That is what
    lets a corridor forecast be projected onto the OSM routing graph in roads.json.
    """
    route = set(ambulance_edges())
    features = []
    for position, segment in enumerate(segments):
        edge = net.getEdge(segment)
        shape = [net.convertXY2LonLat(x, y) for x, y in edge.getShape()]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "segment_id": segment,
                    "index": position,
                    "osm_way_id": segment.lstrip("-").split("#")[0],
                    "reverse": segment.startswith("-"),
                    "name": edge.getName() or "",
                    "length_m": round(float(lengths[position]), 1),
                    "free_flow_mph": round(float(free_flow[position] * MPS_TO_MPH), 2),
                    "on_ambulance_route": segment in route,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[round(lon, 6), round(lat, 6)] for lon, lat in shape],
                },
            }
        )
    destination = WORK / "segments.geojson"
    destination.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return destination


def build(days: int, segment_count: int, seed: int, workers: int) -> Path:
    if not NETWORK.exists():
        raise SystemExit("Generate the network first: python sim/chennai/build_network.py")
    WORK.mkdir(parents=True, exist_ok=True)
    net = sumolib.net.readNet(str(NETWORK))

    print(f"selecting {segment_count} monitored segments from peak-hour probe runs", flush=True)
    segments = select_segments(segment_count, seed)
    free_flow = np.asarray(
        [net.getEdge(segment).getSpeed() for segment in segments], dtype=np.float32
    )
    lengths = np.asarray(
        [net.getEdge(segment).getLength() for segment in segments], dtype=np.float32
    )
    adjacency = build_adjacency(segments)
    print(f"graph density: {float((adjacency > 0).mean()):.3f}", flush=True)

    speeds = np.zeros((days * STEPS_PER_DAY, len(segments)), dtype=np.float32)
    sampled = np.zeros_like(speeds, dtype=bool)
    payloads = [(day, segments, seed, str(Path.cwd())) for day in range(days)]
    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for day_index, day_speed, day_sampled in pool.map(_worker, payloads):
            start = day_index * STEPS_PER_DAY
            speeds[start : start + STEPS_PER_DAY] = day_speed
            sampled[start : start + STEPS_PER_DAY] = day_sampled
            completed += 1
            print(f"simulated day {day_index} ({completed}/{days})", flush=True)

    # An unoccupied segment is not a missing reading: in the simulator it means the
    # road is empty, so its travel speed is the free-flow limit. That convention is
    # recorded in the manifest and keeps the observation mask honest.
    filled_mps = np.where(sampled, speeds, free_flow[None, :])
    values = (filled_mps * MPS_TO_MPH).astype(np.float32)
    observed = np.ones_like(values, dtype=bool)
    timestamps = np.asarray(
        [
            int((EPOCH + timedelta(seconds=step * INTERVAL_S)).timestamp()) * 1_000_000_000
            for step in range(len(values))
        ],
        dtype=np.int64,
    )
    train_end = int(len(values) * 0.7)
    mean = float(values[:train_end].mean())
    std = float(values[:train_end].std())
    if std <= 0:
        raise SystemExit("training speed standard deviation is zero")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    output = PROCESSED / f"{STEM}.npz"
    np.savez_compressed(
        output,
        speed=values,
        filled=values,
        observed=observed,
        timestamps=timestamps,
        ids=np.asarray(segments),
        adjacency=adjacency,
        mean=np.float32(mean),
        std=np.float32(std),
        free_flow_mph=(free_flow * MPS_TO_MPH).astype(np.float32),
        length_m=lengths,
        vehicle_sampled=sampled,
    )
    manifest = {
        "dataset": DATASET_NAME,
        "source": "SUMO microsimulation of the Nungambakkam study area",
        "provenance": "simulated; not field measurement",
        "network": str(NETWORK.relative_to(Path.cwd())),
        "bounding_box": "80.2370,13.0520,80.2600,13.0700",
        "speed_unit": "mph",
        "interval_s": INTERVAL_S,
        "days": days,
        "shape": list(values.shape),
        "segments": len(segments),
        "ambulance_route_segments": sum(1 for s in segments if s in set(ambulance_edges())),
        "vehicle_sampled_fraction": float(sampled.mean()),
        "empty_segment_convention": "free-flow speed limit",
        "hourly_insertion_rates_vph": list(WEEKDAY_RATES),
        "weekend_scale": WEEKEND_SCALE,
        "seed": seed,
        "first_day": EPOCH.date().isoformat(),
        "train_mean": mean,
        "train_std": std,
        "graph": "thresholded Gaussian kernel over forward driving distance",
        "built_at": datetime.now(UTC).isoformat(),
    }
    (PROCESSED / f"{STEM}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_segment_geometry(net, segments, free_flow, lengths)
    print(json.dumps(manifest, indent=2), flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--segments", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=6)
    arguments = parser.parse_args()
    path = build(arguments.days, arguments.segments, arguments.seed, arguments.workers)
    print(f"prepared: {path}")


if __name__ == "__main__":
    main()
