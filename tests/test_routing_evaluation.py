"""Independent correctness, closure, FIFO and numeric-input checks."""

import math
import random

import pytest

from routing import NoPathError, RoadEdge, RouteGraph, SearchStats, should_switch_route
from sim.chennai.roads import RoadNetwork, demo_plan


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_exact_search_against_independent_bellman_ford(algorithm):
    rng = random.Random(71)
    for _ in range(20):
        nodes = [str(i) for i in range(10)]
        edges = [
            RoadEdge(f"{a}-{b}", a, b, rng.uniform(1, 30))
            for a in nodes
            for b in nodes
            if a != b and rng.random() < 0.25
        ]
        distances = {node: math.inf for node in nodes}
        distances["0"] = 0
        for _ in range(len(nodes) - 1):
            for edge in edges:
                distances[edge.target] = min(
                    distances[edge.target], distances[edge.source] + edge.base_travel_time_s
                )
        graph = RouteGraph(edges, nodes)
        if math.isinf(distances["9"]):
            with pytest.raises(NoPathError):
                graph.shortest_path("0", "9", algorithm=algorithm)
        else:
            result = graph.shortest_path("0", "9", algorithm=algorithm)
            assert result.travel_time_s == pytest.approx(distances["9"])


def test_astar_reopens_with_admissible_inconsistent_heuristic():
    graph = RouteGraph(
        [
            RoadEdge("sa", "s", "a", 3),
            RoadEdge("sb", "s", "b", 1),
            RoadEdge("ba", "b", "a", 1),
            RoadEdge("ag", "a", "g", 10),
        ]
    )
    result = graph.shortest_path(
        "s",
        "g",
        algorithm="astar",
        heuristic=lambda node, _: {"s": 0, "a": 0, "b": 11, "g": 0}[node],
    )
    assert result.edge_ids == ("sb", "ba", "ag")
    assert result.travel_time_s == 12


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_entry_time_forecast_changes_route_and_checks_cost(algorithm):
    graph = RouteGraph(
        [
            RoadEdge("sa", "s", "a", 6),
            RoadEdge("ag", "a", "g", 2),
            RoadEdge("sb", "s", "b", 7),
            RoadEdge("bg", "b", "g", 7),
        ]
    )

    # A permanent step increase is FIFO. ag is reached at t=6, not t=0.
    def forecast(edge, entry):
        return 30 if edge.edge_id == "ag" and entry >= 5 else edge.base_travel_time_s

    ordinary = graph.shortest_path("s", "g", algorithm=algorithm)
    future = graph.shortest_path("s", "g", algorithm=algorithm, edge_cost=forecast)
    assert ordinary.edge_ids == ("sa", "ag")
    assert future.edge_ids == ("sb", "bg")
    assert future.travel_time_s == 14


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, -math.inf])
def test_invalid_cost_rejected(value):
    graph = RouteGraph([RoadEdge("sg", "s", "g", 1)])
    with pytest.raises(ValueError, match="finite and positive"):
        graph.shortest_path("s", "g", edge_cost=lambda *_: value)


def test_closure_no_path_zero_length_and_invalid_nodes():
    graph = RouteGraph([RoadEdge("sg", "s", "g", 1)], nodes=["isolated"])
    stats = SearchStats()
    with pytest.raises(NoPathError):
        graph.shortest_path("s", "g", blocked=frozenset(["sg"]), stats=stats)
    assert stats.nodes_explored == 1 and stats.computation_ms >= 0
    assert graph.shortest_path("g", "g").edge_ids == ()
    with pytest.raises(NoPathError):
        graph.shortest_path("g", "s")
    with pytest.raises(ValueError, match="graph nodes"):
        graph.shortest_path("missing", "missing")


def test_chennai_astar_cost_matches_dijkstra_with_fast_predictions_and_closure():
    network = RoadNetwork()
    plan = demo_plan()
    rng = random.Random(18)
    weights = {
        edge_id: edge.travel_s * rng.uniform(0.25, 4)
        for edge_id, (_, edge) in network.edge_index.items()
    }
    cases = [(plan.normal.nodes[0], plan.normal.nodes[-1])]
    cases += [tuple(rng.sample(sorted(network.nodes), 2)) for _ in range(24)]
    for start, goal in cases:
        a = network.route(start, goal, plan.blocked_edges, algorithm="astar", weights=weights)
        d = network.route(start, goal, plan.blocked_edges, weights=weights)
        assert (a is None) == (d is None)
        if a is not None:
            assert a.travel_s == pytest.approx(d.travel_s)
            assert network.validate_path(a, plan.blocked_edges)
            assert a.travel_s == pytest.approx(sum(weights[edge] for edge in a.edges))


def test_hysteresis_boundaries_and_bad_inputs():
    assert should_switch_route(200, 190)  # Exactly 10 seconds AND 5 percent.
    assert not should_switch_route(201, 191)
    assert not should_switch_route(100, 90.001)
    assert not should_switch_route(0, 0)
    for value in (-1, math.nan, math.inf):
        with pytest.raises(ValueError):
            should_switch_route(value, 1)
