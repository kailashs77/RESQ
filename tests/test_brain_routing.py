"""Mission-level checks through the actual forecast decision engine."""

import pytest

from brain.engine import DecisionEngine
from schema import ControlState, RoadEstimate
from sim.chennai.roads import RoadNetwork, demo_plan, distance_m


@pytest.fixture(scope="module")
def network():
    return RoadNetwork()


def static_controls(**changes):
    return ControlState(
        historical_model_enabled=False, spatial_temporal_model_enabled=False, **changes
    )


def test_closure_overrides_hysteresis_and_preserves_connectivity(network):
    engine = DecisionEngine(network)
    ordinary = engine.tick(0, static_controls())
    result = engine.tick(0, static_controls(accident=True))
    active = next(route for route in result.routes if route.selected)
    assert set(active.edge_ids).isdisjoint(engine.plan.blocked_edges)
    assert active.edge_ids != ordinary.routes[0].edge_ids
    assert result.routing_decision.decision == "Reroute around closure"
    assert result.routing_decision.connectivity_valid
    path = network.path_from_edges(engine.plan.normal.nodes[0], tuple(active.edge_ids))
    assert path.nodes[-1] == engine.plan.normal.nodes[-1]
    assert network.validate_path(path, engine.plan.blocked_edges)
    retained = engine.tick(0, static_controls(accident=True))
    assert any(not route.feasible and not route.selected for route in retained.routes)
    assert retained.routing_decision.original_roads == result.routing_decision.original_roads
    assert any("Reroute around closure" in event.message for event in retained.decisions)


def test_no_path_stop_and_recovery(network):
    engine = DecisionEngine(network)
    engine.tick(0, static_controls())
    blocked = [edge.edge_id for edge in network.adjacency[engine.plan.normal.nodes[0]]]
    result = engine.tick(0, static_controls(blocked_edge_ids=blocked))
    assert result.routes == [] and result.selected_route_id is None
    assert result.routing_decision.status == "no_path"
    assert result.vehicle_speed_mps == 0
    position = result.vehicle_position
    later = engine.tick(10, static_controls(blocked_edge_ids=blocked))
    assert later.vehicle_position == position
    recovered = engine.tick(10, static_controls())
    assert recovered.routing_decision.connectivity_valid
    assert recovered.vehicle_position == position


def test_mid_edge_replan_never_jumps_and_current_edge_closure_stops(network):
    engine = DecisionEngine(network)
    engine.tick(0, static_controls())
    moving = engine.tick(0.1, static_controls())
    assert engine._offset_m > 0
    position = moving.vehicle_position
    committed = engine._remaining[0]
    rerouted = engine.tick(0.1, static_controls(accident=True))
    assert rerouted.vehicle_position == position
    assert rerouted.routes[0].edge_ids[0] == committed
    stopped = engine.tick(0.1, static_controls(blocked_edge_ids=[committed]))
    assert stopped.routing_decision.status == "no_path"
    assert not stopped.routes
    assert stopped.vehicle_position == position
    later = engine.tick(2, static_controls(blocked_edge_ids=[committed]))
    assert later.vehicle_position == position
    resumed = engine.tick(2, static_controls())
    assert resumed.vehicle_position == position
    assert resumed.routes[0].edge_ids[0] == committed
    advanced = engine.tick(2.1, static_controls())
    assert distance_m(advanced.vehicle_position, position) <= 0.86


def test_actual_baseline_forecasts_change_selected_path(network):
    engine = DecisionEngine(network)
    ordinary = engine.tick(0, ControlState())
    congested = engine.tick(0, ControlState(congestion_scenario=True))
    assert congested.routes[0].edge_ids != ordinary.routes[0].edge_ids
    assert congested.routing_decision.connectivity_valid
    assert congested.routing_decision.decision == "Reroute for predicted congestion"
    assert congested.routing_decision.saving_s >= 10
    for controls in (
        ControlState(historical_model_enabled=False),
        ControlState(spatial_temporal_model_enabled=False),
        static_controls(),
    ):
        single = DecisionEngine(network).tick(0, controls)
        assert single.routing_decision.connectivity_valid


def test_arrival_does_not_loop_back_to_origin(network):
    engine = DecisionEngine(network)
    engine.tick(0, static_controls())
    arrived = engine.tick(10000, static_controls())
    assert arrived.routing_decision.status == "arrived"
    assert arrived.vehicle_position == demo_plan().hospital
    assert arrived.vehicle_speed_mps == 0
    assert engine.tick(10001, static_controls()).vehicle_position == arrived.vehicle_position


def test_hysteresis_retains_connected_route_under_small_forecast_improvement(network, monkeypatch):
    engine = DecisionEngine(network)
    original = engine.tick(0, static_controls()).routes[0].edge_ids
    affected = set(engine.plan.normal.edges) & engine.plan.blocked_edges
    estimates = {
        edge_id: RoadEstimate(
            road_id=edge_id,
            travel_time_s=network.edge_index[edge_id][1].travel_s + 20 / len(affected),
            confidence=0.9,
            explanation="Controlled test prediction",
        )
        for edge_id in affected
    }
    monkeypatch.setattr(engine, "_road_estimates", lambda *_: estimates)
    for _ in range(10):
        output = engine.tick(0, ControlState())
        assert output.routes[0].edge_ids == original
        assert output.routing_decision.decision == "Retain current route"
        assert output.routing_decision.saving_s > 0
        assert output.routing_decision.connectivity_valid


def test_forecast_failure_falls_back_and_parallel_snapshots_are_serialized(network, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from brain.simulation import BrainSimulation

    engine = DecisionEngine(network)

    def fail(*_args, **_kwargs):
        raise RuntimeError("forecaster unavailable")

    monkeypatch.setattr(engine.forecast.historical, "predict", fail)
    assert engine.tick(0, ControlState()).routing_decision.connectivity_valid
    monkeypatch.setattr(engine.forecast.spatial_temporal, "predict", fail)
    result = engine.tick(0, ControlState())
    assert result.routing_decision.weight_source.startswith("Static fallback")
    assert result.routing_decision.connectivity_valid
    simulation = BrainSimulation()
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: simulation.snapshot(), range(12)))
    assert all(s.routing_decision.connectivity_valid for s in snapshots)
