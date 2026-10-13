"""Exact Dijkstra/A* with closures and edge-entry-time costs.

Callbacks must be FIFO: entering an edge later cannot yield earlier arrival.
Heuristics must lower-bound remaining cost at every entry time. Use zero if no
bound is known. Non-FIFO routing and waiting are outside this implementation.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from heapq import heappop, heappush
from math import inf, isfinite
from time import perf_counter_ns


class NoPathError(ValueError):
    """A valid query has no connected, open path."""


@dataclass(frozen=True, slots=True)
class RoadEdge:
    edge_id: str
    source: str
    target: str
    base_travel_time_s: float


@dataclass(slots=True)
class SearchStats:
    nodes_explored: int = 0  # Non-stale expansions, including goal.
    edges_examined: int = 0
    computation_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class PathResult:
    edge_ids: tuple[str, ...]
    travel_time_s: float


EdgeCost = Callable[[RoadEdge, float], float]
Heuristic = Callable[[str, str], float]


class RouteGraph:
    def __init__(self, edges: Iterable[RoadEdge], nodes: Iterable[str] = ()) -> None:
        self._adjacency: dict[str, list[RoadEdge]] = defaultdict(list)
        self.nodes = set(nodes)
        ids: set[str] = set()
        for edge in edges:
            if not isfinite(edge.base_travel_time_s) or edge.base_travel_time_s <= 0:
                raise ValueError("edge travel time must be finite and positive")
            if edge.edge_id in ids:
                raise ValueError(f"duplicate edge id: {edge.edge_id}")
            ids.add(edge.edge_id)
            self.nodes.update((edge.source, edge.target))
            self._adjacency[edge.source].append(edge)

    def shortest_path(
        self,
        start: str,
        goal: str,
        departure_time_s: float = 0,
        edge_cost: EdgeCost | None = None,
        *,
        algorithm: str = "dijkstra",
        heuristic: Heuristic | None = None,
        blocked: frozenset[str] = frozenset(),
        stats: SearchStats | None = None,
    ) -> PathResult:
        """Fresh exact search. A* with zero heuristic reduces to Dijkstra.

        Closures are excluded, never assigned a large cost. Disconnection raises
        NoPathError. The caller guarantees FIFO callbacks/admissible heuristics.
        """
        if algorithm not in {"dijkstra", "astar"}:
            raise ValueError("algorithm must be dijkstra or astar")
        if start not in self.nodes or goal not in self.nodes:
            raise ValueError("start and goal must be graph nodes")
        if not isfinite(departure_time_s) or departure_time_s < 0:
            raise ValueError("departure time must be finite and nonnegative")
        metrics = stats if stats is not None else SearchStats()
        metrics.nodes_explored = metrics.edges_examined = 0
        metrics.computation_ms = 0.0
        began = perf_counter_ns()
        cost_fn = edge_cost or (lambda edge, _: edge.base_travel_time_s)
        cached: dict[str, float] = {}

        def h(node: str) -> float:
            if node not in cached:
                value = heuristic(node, goal) if algorithm == "astar" and heuristic else 0.0
                if not isfinite(value) or value < 0 or (node == goal and value != 0):
                    raise ValueError("heuristic must be finite, nonnegative and zero at goal")
                cached[node] = value
            return cached[node]

        try:
            h(goal)
            best: dict[str, float] = {start: 0.0}
            previous: dict[str, RoadEdge] = {}
            queue = [(h(start), 0.0, start)]
            while queue:
                _, elapsed, node = heappop(queue)
                if elapsed > best.get(node, inf):
                    continue
                metrics.nodes_explored += 1
                if node == goal:
                    path: list[str] = []
                    while node != start:
                        edge = previous[node]
                        path.append(edge.edge_id)
                        node = edge.source
                    return PathResult(tuple(reversed(path)), elapsed)
                for edge in self._adjacency.get(node, ()):
                    metrics.edges_examined += 1
                    if edge.edge_id in blocked:
                        continue
                    travel_time = cost_fn(edge, departure_time_s + elapsed)
                    if not isfinite(travel_time) or travel_time <= 0:
                        raise ValueError("edge cost must be finite and positive")
                    candidate = elapsed + travel_time
                    if not isfinite(candidate):
                        raise ValueError("path cost overflow")
                    if candidate < best.get(edge.target, inf):
                        best[edge.target] = candidate
                        previous[edge.target] = edge
                        heappush(queue, (candidate + h(edge.target), candidate, edge.target))
            raise NoPathError(f"no route from {start!r} to {goal!r}")
        finally:
            metrics.computation_ms = (perf_counter_ns() - began) / 1_000_000


def should_switch_route(
    current_eta_s: float,
    candidate_eta_s: float,
    minimum_saving_s: float = 10.0,
    minimum_saving_fraction: float = 0.05,
) -> bool:
    """Both margins must pass. Infeasible routes bypass this ETA-only policy."""
    values = (current_eta_s, candidate_eta_s, minimum_saving_s, minimum_saving_fraction)
    if not all(isfinite(value) and value >= 0 for value in values) or minimum_saving_fraction > 1:
        raise ValueError("ETAs and thresholds must be finite and nonnegative")
    if current_eta_s == 0:
        return False
    saving = current_eta_s - candidate_eta_s
    return (
        saving > 0
        and saving >= minimum_saving_s
        and (saving / current_eta_s >= minimum_saving_fraction)
    )
