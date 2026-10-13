"""Generate the SUMO ambulance route with the repository's Dijkstra router."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from math import asin, cos, pi, radians, sin, sqrt
from pathlib import Path

# This file is intentionally runnable directly from PowerShell, like the
# existing network builder, so make the project package importable first.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from routing import RoadEdge, RouteGraph
from schema import GeoPoint
from sim.chennai.roads import HOSPITAL, ORIGIN

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
DEFAULT_NET = GENERATED / "thousand_lights.net.xml"
DEFAULT_OUTPUT = GENERATED / "ambulance.rou.xml"
DEFAULT_ADDITIONALS = GENERATED / "scenario.add.xml"
ROAD_DATA = HERE / "roads.json"
START_LABEL = "Nungambakkam ambulance base (approx.)"
DESTINATION_LABEL = "Apollo Hospitals, Greams Road (approx.)"
ROUTE_LABEL = "resq_optimal_route"


@dataclass(frozen=True, slots=True)
class SumoEdge:
    edge_id: str
    source: str
    target: str
    travel_time_s: float
    shape: tuple[tuple[float, float], ...]


def _distance_m(lat: float, lon: float, point: tuple[float, float]) -> float:
    other_lat, other_lon = point
    lat1, lat2 = radians(lat), radians(other_lat)
    dlat, dlon = lat2 - lat1, radians(other_lon - lon)
    value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 12_742_000 * asin(min(1, sqrt(value)))


def load_edges(net_path: Path) -> tuple[dict[str, SumoEdge], dict[str, tuple[float, float]]]:
    """Load normal SUMO edges and their free-flow travel times."""

    root = ET.parse(net_path).getroot()
    junctions = {
        item.get("id", ""): (float(item.get("x", "0")), float(item.get("y", "0")))
        for item in root.findall("junction")
        if item.get("id")
    }
    edges: dict[str, SumoEdge] = {}
    for element in root.findall("edge"):
        edge_id = element.get("id")
        source = element.get("from")
        target = element.get("to")
        if not edge_id or edge_id.startswith(":") or not source or not target:
            continue
        lanes = element.findall("lane")
        if not lanes:
            continue
        # SUMO edge lanes have the same geometry; use the fastest lane so the
        # ambulance route is optimized for the available road speed limit.
        lane_times: list[tuple[float, tuple[tuple[float, float], ...]]] = []
        for lane in lanes:
            speed = float(lane.get("speed", "0"))
            length = float(lane.get("length", "0"))
            if speed > 0 and length > 0:
                points = tuple(
                    tuple(map(float, pair.split(",")))
                    for pair in lane.get("shape", "").split()
                )
                lane_times.append((length / speed, points))
        if lane_times:
            travel_time, shape = min(lane_times, key=lambda item: item[0])
            edges[edge_id] = SumoEdge(edge_id, source, target, travel_time, shape)
    if not edges:
        raise ValueError(f"No drivable SUMO edges found in {net_path}")
    return edges, junctions


def _road_node_locations() -> dict[str, tuple[float, float]]:
    import json

    payload = json.loads(ROAD_DATA.read_text(encoding="utf-8"))
    return {node_id: (coords[0], coords[1]) for node_id, coords in payload["nodes"].items()}


def nearest_network_node(
    point: GeoPoint, road_nodes: dict[str, tuple[float, float]], edges: dict[str, SumoEdge]
) -> str:
    connected = {edge.source for edge in edges.values()} | {edge.target for edge in edges.values()}
    candidates = [node_id for node_id in connected if node_id in road_nodes]
    if not candidates:
        raise ValueError("No OSM road nodes could be matched to the generated SUMO network")
    return min(
        candidates,
        key=lambda node_id: _distance_m(point.lat, point.lon, road_nodes[node_id]),
    )


def write_ambulance_route(route_path: Path, edge_ids: tuple[str, ...]) -> None:
    root = ET.Element("routes")
    vehicle = ET.SubElement(
        root,
        "vehicle",
        {
            "id": "resq_ambulance",
            "type": "ambulance",
            "depart": "0",
            "departLane": "best",
            "departSpeed": "max",
        },
    )
    ET.SubElement(vehicle, "route", {"edges": " ".join(edge_ids)})
    ET.indent(root, space="    ")
    route_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(route_path, encoding="UTF-8", xml_declaration=True)


def _circle(x: float, y: float, radius: float, points: int = 24) -> str:
    return " ".join(
        f"{x + radius * cos(2 * pi * i / points):.2f},{y + radius * sin(2 * pi * i / points):.2f}"
        for i in range(points)
    )


def write_scenario_additionals(
    path: Path,
    edge_ids: tuple[str, ...],
    edges: dict[str, SumoEdge],
    junctions: dict[str, tuple[float, float]],
    start_node: str,
    goal_node: str,
) -> None:
    root = ET.Element("additional")
    for poi_id, label, node_id, color in (
        ("ambulance_start", f"AMBULANCE START - {START_LABEL}", start_node, "0,0.57,1"),
        ("apollo_hospital", f"HOSPITAL - {DESTINATION_LABEL}", goal_node, "1,0.47,0"),
    ):
        x, y = junctions[node_id]
        # sumo-gui does not reliably draw plain POIs, so mark each end with a
        # white-ringed disc polygon; the POI keeps the label for hover/inspection.
        for suffix, radius, disc_color, layer in (
            ("ring", 19.0, "1,1,1", "101"),
            ("disc", 14.0, color, "102"),
        ):
            ET.SubElement(
                root,
                "poly",
                {
                    "id": f"{poi_id}_{suffix}",
                    "type": "scenario_marker",
                    "color": disc_color,
                    "layer": layer,
                    "fill": "true",
                    "shape": _circle(x, y, radius),
                },
            )
        ET.SubElement(
            root,
            "poi",
            {
                "id": poi_id,
                "type": label,
                "x": f"{x:.2f}",
                "y": f"{y:.2f}",
                "color": color,
                "layer": "100",
                "width": "24",
                "height": "24",
            },
        )

    route_shape: list[tuple[float, float]] = []
    for edge_id in edge_ids:
        for point in edges[edge_id].shape:
            if not route_shape or point != route_shape[-1]:
                route_shape.append(point)
    ET.SubElement(
        root,
        "poly",
        {
            "id": ROUTE_LABEL,
            "type": "ambulance_route",
            # Translucent, above the roads but below vehicles (layer 20) so vehicles,
            # including the ambulance, are drawn on top of the route line.
            "color": "0,0.82,0.42,0.55",
            "layer": "20",
            "fill": "false",
            "lineWidth": "2.3",
            "shape": " ".join(f"{x:.2f},{y:.2f}" for x, y in route_shape),
        },
    )
    ET.indent(root, space="    ")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def build_route(net_path: Path, output: Path, additionals: Path) -> tuple[str, ...]:
    edges, junctions = load_edges(net_path)
    road_nodes = _road_node_locations()
    start_node = nearest_network_node(ORIGIN, road_nodes, edges)
    goal_node = nearest_network_node(HOSPITAL, road_nodes, edges)
    graph = RouteGraph(
        RoadEdge(item.edge_id, item.source, item.target, item.travel_time_s)
        for item in edges.values()
    )
    result = graph.shortest_path(start_node, goal_node)
    route_edges = result.edge_ids
    if not route_edges:
        raise ValueError("The selected ambulance start and destination resolve to one network node")
    write_ambulance_route(output, route_edges)
    write_scenario_additionals(additionals, route_edges, edges, junctions, start_node, goal_node)
    return route_edges


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--additionals", type=Path, default=DEFAULT_ADDITIONALS)
    args = parser.parse_args()
    route = build_route(args.net, args.output, args.additionals)
    print(f"ResQ Dijkstra ambulance route written to {args.output}")
    print(f"Edges: {len(route)}")
    print(f"Start: {START_LABEL}")
    print(f"Destination: {DESTINATION_LABEL}")
    print(f"Route overlay: {args.additionals}")


if __name__ == "__main__":
    main()
