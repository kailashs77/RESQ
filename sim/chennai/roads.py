"""Directed, road-following routing over the checked-in Chennai OSM extract.

This is a demonstration router, not turn-restriction-aware navigation. It respects
mapped one-way streets and motor-vehicle exclusions at extract time.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path

from routing import NoPathError, RouteGraph, SearchStats
from routing import RoadEdge as SearchEdge
from schema import GeoPoint

DATA = Path(__file__).with_name("roads.json")
ORIGIN = GeoPoint(lat=13.0624, lon=80.2370)
HOSPITAL = GeoPoint(lat=13.0606, lon=80.2515)


def distance_m(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lat2 = radians(a.lat), radians(b.lat)
    dlat, dlon = lat2 - lat1, radians(b.lon - a.lon)
    value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 12_742_000 * asin(min(1, sqrt(value)))


@dataclass(frozen=True)
class RoadEdge:
    target: str
    edge_id: str
    name: str
    length_m: float
    travel_s: float


@dataclass(frozen=True)
class RoadPath:
    nodes: tuple[str, ...]
    edges: tuple[str, ...]
    names: tuple[str, ...]
    distance_m: float
    travel_s: float
    geometry: tuple[GeoPoint, ...]


@dataclass(frozen=True)
class IncidentPlan:
    normal: RoadPath
    bypass: RoadPath
    incident: GeoPoint
    blocked_edges: frozenset[str]
    origin: GeoPoint
    hospital: GeoPoint


def point_along(path: RoadPath, progress: float) -> GeoPoint:
    """Interpolate by distance along graph edges, never across road blocks."""
    remaining = max(0.0, min(1.0, progress)) * path.distance_m
    for a, b in zip(path.geometry, path.geometry[1:], strict=False):
        length = distance_m(a, b)
        if remaining <= length:
            fraction = remaining / length if length else 0.0
            return GeoPoint(
                lat=a.lat + (b.lat - a.lat) * fraction, lon=a.lon + (b.lon - a.lon) * fraction
            )
        remaining -= length
    return path.geometry[-1]


class RoadNetwork:
    def __init__(self, path: Path = DATA) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.nodes = {
            node_id: GeoPoint(lat=coords[0], lon=coords[1])
            for node_id, coords in payload["nodes"].items()
        }
        self.adjacency: dict[str, list[RoadEdge]] = {node_id: [] for node_id in self.nodes}
        for way in payload["ways"]:
            refs = way["nodes"]
            oneway = way["oneway"]
            # These are deliberately conservative assumed demo speeds, not OSM maxspeed.
            speed_mps = 5.5 if way["highway"] in {"service", "living_street"} else 8.5
            for index, (a, b) in enumerate(zip(refs, refs[1:], strict=False)):
                length = distance_m(self.nodes[a], self.nodes[b])
                if length < 0.1:
                    continue
                base_id = f"{way['id']}:{index}"
                if oneway != "-1":
                    self.adjacency[a].append(
                        RoadEdge(b, f"{base_id}:+", way["name"], length, length / speed_mps)
                    )
                if oneway not in {"yes", "1", "true"}:
                    self.adjacency[b].append(
                        RoadEdge(a, f"{base_id}:-", way["name"], length, length / speed_mps)
                    )

        self.edge_index = {
            edge.edge_id: (source, edge)
            for source, edges in self.adjacency.items()
            for edge in edges
        }
        self.graph = RouteGraph(
            (
                SearchEdge(edge.edge_id, source, edge.target, edge.travel_s)
                for source, edges in self.adjacency.items()
                for edge in edges
            ),
            nodes=self.nodes,
        )

    def nearest_node(self, point: GeoPoint) -> str:
        return min(self.nodes, key=lambda node_id: distance_m(point, self.nodes[node_id]))

    def route(
        self,
        start: str,
        finish: str,
        blocked: frozenset[str] = frozenset(),
        *,
        algorithm: str = "dijkstra",
        weights: Mapping[str, float] | None = None,
        departure_time_s: float = 0.0,
        stats: SearchStats | None = None,
    ) -> RoadPath | None:
        """Search the full directed graph with one immutable weight snapshot.

        A* uses great-circle distance times a proven lower cost/metre bound,
        recalculated for predictions (including predictions faster than static).
        Unknown/invalid inputs raise ValueError; only disconnection returns None.
        """
        weights = weights or {}
        if set(weights) - self.edge_index.keys() or set(blocked) - self.edge_index.keys():
            raise ValueError("unknown edge in weights or closure set")
        if any(not isfinite(cost) or cost <= 0 for cost in weights.values()):
            raise ValueError("weights must be finite and positive")
        lower_bound = (
            min(
                (
                    weights.get(edge_id, edge.travel_s) / edge.length_m
                    for edge_id, (_, edge) in self.edge_index.items()
                    if edge_id not in blocked
                ),
                default=0.0,
            )
            if algorithm == "astar"
            else 0.0
        )
        try:
            result = self.graph.shortest_path(
                start,
                finish,
                departure_time_s,
                edge_cost=lambda edge, _: weights.get(edge.edge_id, edge.base_travel_time_s),
                algorithm=algorithm,
                heuristic=lambda a, b: distance_m(self.nodes[a], self.nodes[b]) * lower_bound,
                blocked=blocked,
                stats=stats,
            )
        except NoPathError:
            return None
        return self.path_from_edges(start, result.edge_ids, weights)

    def path_from_edges(
        self,
        start: str,
        edge_ids: tuple[str, ...],
        weights: Mapping[str, float] | None = None,
    ) -> RoadPath:
        """Reconstruct only a connected sequence of actual directed road edges."""
        weights = weights or {}
        nodes = [start]
        edges = []
        for edge_id in edge_ids:
            source, edge = self.edge_index[edge_id]
            if source != nodes[-1]:
                raise ValueError(f"disconnected road sequence at {edge_id}")
            nodes.append(edge.target)
            edges.append(edge)
        return RoadPath(
            nodes=tuple(nodes),
            edges=tuple(edge.edge_id for edge in edges),
            names=tuple(edge.name for edge in edges),
            distance_m=sum(edge.length_m for edge in edges),
            travel_s=sum(weights.get(edge.edge_id, edge.travel_s) for edge in edges),
            geometry=tuple(self.nodes[node] for node in nodes),
        )

    def validate_path(self, path: RoadPath, blocked: frozenset[str] = frozenset()) -> bool:
        """Prove every adjacent pair is a mapped directed edge and is open."""
        if len(path.nodes) != len(path.edges) + 1 or len(path.geometry) != len(path.nodes):
            return False
        if set(path.edges) & blocked:
            return False
        return all(
            self.nodes.get(node) == point
            for node, point in zip(path.nodes, path.geometry, strict=True)
        ) and all(
            edge_id in self.edge_index
            and self.edge_index[edge_id][0] == source
            and self.edge_index[edge_id][1].target == target
            for source, target, edge_id in zip(
                path.nodes[:-1], path.nodes[1:], path.edges, strict=True
            )
        )


@lru_cache(maxsize=1)
def road_geojson() -> dict:
    """The same road ways used for routing, exposed for map inspection."""
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": way["id"], "name": way["name"], "highway": way["highway"]},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [payload["nodes"][ref][1], payload["nodes"][ref][0]] for ref in way["nodes"]
                    ],
                },
            }
            for way in payload["ways"]
        ],
    }


@lru_cache(maxsize=1)
def demo_plan() -> IncidentPlan:
    network = RoadNetwork()
    start = network.nearest_node(ORIGIN)
    finish = network.nearest_node(HOSPITAL)
    normal = network.route(start, finish)
    if normal is None:
        raise RuntimeError("The demo origin and hospital are disconnected in the road graph")

    # Select a repeatable section of the normal route that yields a useful,
    # physically connected bypass. Disable both directions across that section.
    cumulative = 0.0
    candidates: list[tuple[float, RoadPath, frozenset[str], GeoPoint]] = []
    for index in range(len(normal.edges)):
        if index:
            cumulative += distance_m(normal.geometry[index - 1], normal.geometry[index])
        if not 0.20 <= cumulative / normal.distance_m <= 0.60:
            continue
        section: list[str] = []
        section_length = 0.0
        for j in range(index, len(normal.edges)):
            section.append(normal.edges[j])
            section_length += distance_m(normal.geometry[j], normal.geometry[j + 1])
            if section_length >= 100:
                break
        if section_length < 65:
            continue
        blocked = frozenset(
            edge_id[:-1] + sign
            for edge_id in section
            for sign in ("+", "-")
            if edge_id[:-1] + sign in network.edge_index
        )
        bypass = network.route(start, finish, blocked)
        if bypass is None or bypass.travel_s <= normal.travel_s + 5:
            continue
        if bypass.travel_s > normal.travel_s + 100:
            continue
        overlap = len(set(normal.edges) & set(bypass.edges)) / len(normal.edges)
        if overlap > 0.85:
            continue
        score = abs((bypass.travel_s - normal.travel_s) - 28) + overlap * 25
        candidates.append((score, bypass, blocked, normal.geometry[index]))
    if not candidates:
        raise RuntimeError("No suitable connected incident bypass exists in the extract")
    _, bypass, blocked, incident = min(candidates, key=lambda item: item[0])
    return IncidentPlan(
        normal=normal,
        bypass=bypass,
        incident=incident,
        blocked_edges=blocked,
        origin=network.nodes[start],
        hospital=network.nodes[finish],
    )
