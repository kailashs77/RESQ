"""Measured routing evidence, separate from formula-based B0-B5 estimates.

Run: uv run python -m experiments.routing_evaluation --repeats 100
Congestion, closures and speeds are synthetic inputs. Wall times are measured.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import random
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from math import ceil, isclose
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from brain.engine import DecisionEngine
from routing import NoPathError, SearchStats, should_switch_route
from schema import ControlState
from sim.chennai.roads import DATA, RoadNetwork, demo_plan, distance_m

ROOT = Path(__file__).resolve().parents[1]


def connectivity_proof(network, path, blocked):
    if not network.validate_path(path, blocked):
        raise AssertionError("invalid road path")
    return {
        "valid": True,
        "start": path.nodes[0],
        "goal": path.nodes[-1],
        "node_count": len(path.nodes),
        "edge_count": len(path.edges),
        "geometry_count": len(path.geometry),
        "blocked_intersection": [],
        "edges": [
            {
                "index": i,
                "edge_id": edge_id,
                "source": a,
                "target": b,
                "road": network.edge_index[edge_id][1].name,
                "directed_edge_exists": True,
                "open": edge_id not in blocked,
            }
            for i, (a, b, edge_id) in enumerate(
                zip(path.nodes[:-1], path.nodes[1:], path.edges, strict=True)
            )
        ],
    }


def evaluate(repeats=100):
    if repeats < 2:
        raise ValueError("at least two repeated measurements are required")
    network, plan = RoadNetwork(), demo_plan()
    start, goal = plan.normal.nodes[0], plan.normal.nodes[-1]
    engine = DecisionEngine(network, plan)
    estimates = engine._road_estimates(0, ControlState(congestion_scenario=True))
    predicted = {key: item.travel_time_s for key, item in estimates.items()}
    isolated = frozenset(edge.edge_id for edge in network.adjacency[start])
    scenarios = [
        ("Static baseline", frozenset(), {}, None, 0),
        ("Accident closure", plan.blocked_edges, {}, None, 0),
        ("Predicted congestion", frozenset(), predicted, None, 0),
        ("FIFO forecast departure 0s", frozenset(), {}, "fifo", 0),
        ("FIFO forecast departure 600s", frozenset(), {}, "fifo", 600),
        ("No path: all origin exits closed", isolated, {}, None, 0),
    ]
    rows, samples, proofs, paths = [], [], {}, {}

    def run(algorithm, blocked, weights, mode, departure):
        stats = SearchStats()
        began = perf_counter_ns()
        if mode == "fifo":
            # Arrival derivative > 0: increasing entry time increases arrival.
            def cost(edge, entry):
                factor = 1 + max(0, entry - 400) * 0.02 if edge.edge_id in plan.blocked_edges else 1
                return edge.base_travel_time_s * factor

            try:
                result = network.graph.shortest_path(
                    start,
                    goal,
                    departure,
                    edge_cost=cost,
                    algorithm=algorithm,
                    heuristic=lambda a, b: distance_m(network.nodes[a], network.nodes[b]) / 8.5,
                    blocked=blocked,
                    stats=stats,
                )
            except NoPathError:
                path = None
            else:
                path = replace(
                    network.path_from_edges(start, result.edge_ids), travel_s=result.travel_time_s
                )
            wall_ms = (perf_counter_ns() - began) / 1_000_000
            if path is not None:
                elapsed = 0.0
                for edge_id in path.edges:
                    edge = network.edge_index[edge_id][1]
                    factor = (
                        1 + max(0, departure + elapsed - 400) * 0.02
                        if edge_id in plan.blocked_edges
                        else 1
                    )
                    elapsed += edge.travel_s * factor
                if not isclose(elapsed, path.travel_s, rel_tol=1e-10):
                    raise AssertionError("FIFO cost does not match edge-entry evaluation")
        else:
            path = network.route(
                start,
                goal,
                blocked,
                algorithm=algorithm,
                weights=weights,
                departure_time_s=departure,
                stats=stats,
            )
            wall_ms = (perf_counter_ns() - began) / 1_000_000
        return path, stats, wall_ms

    rng = random.Random(77)
    for scenario, blocked, weights, mode, departure in scenarios:
        for algorithm in ("dijkstra", "astar"):
            for _ in range(5):
                run(algorithm, blocked, weights, mode, departure)
        measured = {algorithm: [] for algorithm in ("dijkstra", "astar")}
        canonical, first_edges = {}, {}
        for repeat in range(repeats):
            order = ["dijkstra", "astar"]
            rng.shuffle(order)
            for algorithm in order:
                path, stats, wall_ms = run(algorithm, blocked, weights, mode, departure)
                if path is not None:
                    if not network.validate_path(path, blocked):
                        raise AssertionError("disconnected or blocked output")
                    if mode != "fifo" and not isclose(
                        path.travel_s,
                        sum(
                            weights.get(edge_id, network.edge_index[edge_id][1].travel_s)
                            for edge_id in path.edges
                        ),
                        rel_tol=1e-10,
                    ):
                        raise AssertionError("path cost mismatch")
                edges = path.edges if path else None
                if algorithm in first_edges and first_edges[algorithm] != edges:
                    raise AssertionError("nondeterministic path")
                first_edges[algorithm] = edges
                canonical[algorithm] = path
                sample = {
                    "scenario": scenario,
                    "algorithm": algorithm,
                    "repeat": repeat,
                    "computation_ms": wall_ms,
                    "search_ms": stats.computation_ms,
                    "nodes_explored": stats.nodes_explored,
                    "edges_examined": stats.edges_examined,
                    "travel_time_s": path.travel_s if path else None,
                }
                measured[algorithm].append(sample)
                samples.append(sample)
            a, d = canonical["astar"], canonical["dijkstra"]
            if (a is None) != (d is None) or (
                a
                and not isclose(
                    a.travel_s,
                    d.travel_s,
                    rel_tol=1e-10,
                )
            ):
                raise AssertionError("A* and Dijkstra disagree on optimal cost")
        for algorithm, values in measured.items():
            path = canonical[algorithm]
            times = sorted(value["computation_ms"] for value in values)
            rows.append(
                {
                    "scenario": scenario,
                    "algorithm": algorithm,
                    "repeats": repeats,
                    "median_ms": median(times),
                    "p95_ms": times[ceil(0.95 * len(times)) - 1],
                    "median_search_ms": median(value["search_ms"] for value in values),
                    "nodes_explored": values[0]["nodes_explored"],
                    "edges_examined": values[0]["edges_examined"],
                    "travel_time_s": path.travel_s if path else None,
                    "status": "ok" if path else "no_path",
                    "equal_optimal_cost": True,
                }
            )
        if canonical["astar"]:
            paths[scenario] = canonical["astar"]
            proofs[scenario] = connectivity_proof(network, canonical["astar"], blocked)

    sequence = [(100, 96), (98, 100)] * 20 + [(125, 100)] * 5

    def switching(use_hysteresis):
        selected, switches, regret = 0, 0, 0.0
        for a, b in sequence:
            costs = (a, b)
            candidate = 1 - selected
            switch = (
                should_switch_route(costs[selected], costs[candidate])
                if use_hysteresis
                else costs[candidate] < costs[selected]
            )
            if switch:
                selected = candidate
                switches += 1
            regret += costs[selected] - min(costs)
        return {
            "switches": switches,
            "total_retained_extra_cost_s": regret,
            "final_selected_route": selected,
        }

    hysteresis = {
        "samples": len(sequence),
        "input_sequence": sequence,
        "without_hysteresis": switching(False),
        "with_hysteresis": switching(True),
        "minimum_saving_s": 10,
        "minimum_saving_fraction": 0.05,
        "interpretation": "Extra cost sums illustrative decisions, not a journey metric.",
    }
    controls = ControlState(historical_model_enabled=False, spatial_temporal_model_enabled=False)
    mission = DecisionEngine(network, plan)
    initial = mission.tick(0, controls)
    accident = mission.tick(0, controls.model_copy(update={"accident": True}))
    no_path = mission.tick(0, controls.model_copy(update={"blocked_edge_ids": sorted(isolated)}))
    dynamic = {
        "initial": initial.routing_decision.model_dump(),
        "closure": accident.routing_decision.model_dump(),
        "no_path": no_path.routing_decision.model_dump(),
        "closure_overrides_margin": accident.routing_decision.decision == "Reroute around closure",
        "no_path_emits_no_route": no_path.routes == [] and no_path.selected_route_id is None,
    }
    if not all(
        (
            dynamic["closure_overrides_margin"],
            dynamic["no_path_emits_no_route"],
            accident.routing_decision.connectivity_valid,
        )
    ):
        raise AssertionError("mission checks failed")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    report = {
        "generated_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
        "metadata": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "nodes": len(network.nodes),
            "directed_edges": len(network.edge_index),
            "repeats": repeats,
            "warmups": 5,
            "shuffle_seed": 77,
            "base_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "road_graph_sha256": digest(DATA),
            "source_sha256": {
                str(p.relative_to(ROOT)).replace("\\", "/"): digest(p)
                for p in (
                    ROOT / "routing/engine.py",
                    ROOT / "sim/chennai/roads.py",
                    ROOT / "brain/engine.py",
                    Path(__file__),
                )
            },
            "timing_scope": "Query, heuristic preparation and path reconstruction. "
            "Excludes graph loading, forecast fitting, validation, API and rendering.",
            "search_timing_scope": "Heap search including heuristic evaluation.",
            "traffic_evidence": "Synthetic controlled inputs, not real traffic/Graph WaveNet/SUMO",
            "heuristic": "Great-circle distance times minimum open-edge cost/metre for snapshots. "
            "FIFO case uses distance/8.5 (weights never below static).",
            "validity_scope": "Mapped directed edges and one-way connectivity. "
            "Turn restrictions, lanes and legal permissions excluded.",
            "d_star_lite": "Future work, not implemented or benchmarked",
        },
        "comparison": rows,
        "raw_samples": samples,
        "connectivity_proofs": proofs,
        "hysteresis": hysteresis,
        "dynamic_checks": dynamic,
        "route_changes": {
            "closure_changes_path": paths["Static baseline"].edges
            != paths["Accident closure"].edges,
            "forecast_changes_path": paths["Static baseline"].edges
            != paths["Predicted congestion"].edges,
            "departure_changes_path": paths["FIFO forecast departure 0s"].edges
            != paths["FIFO forecast departure 600s"].edges,
        },
        "predicted_weights": predicted,
        "closure_edge_ids": sorted(plan.blocked_edges),
    }
    return report, network, plan, paths


def main():
    from experiments.routing_report import write_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/routing-evaluation")
    args = parser.parse_args()
    report, network, plan, paths = evaluate(args.repeats)
    write_report(args.output, report, network, plan, paths)
    for row in report["comparison"]:
        print(
            f"{row['scenario']}: {row['algorithm']} {row['median_ms']:.3f}ms, "
            f"{row['nodes_explored']} nodes, cost={row['travel_time_s']}"
        )
    print(f"Evidence written to {args.output}")


if __name__ == "__main__":
    main()
