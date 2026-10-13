"""The trained forecast must reach the route decision without breaking the demo."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.main import app
from forecast.runtime import CorridorState, SegmentForecast, horizon_for_eta, way_of
from schema import GeoPoint
from sim import DemoSimulation
from sim.chennai.pricing import price_path
from sim.chennai.roads import RoadPath

HORIZON = 15


def _segment(segment_id: str, speed_mph: float, length_m: float = 200.0) -> SegmentForecast:
    way, _ = way_of(segment_id)
    return SegmentForecast(
        segment_id=segment_id,
        osm_way_id=way,
        name=f"Way {way}",
        length_m=length_m,
        free_flow_mph=30.0,
        on_route=True,
        observed_mph=speed_mph,
        speeds_mph={5: speed_mph, 15: speed_mph, 30: speed_mph, 60: speed_mph},
        stddev_mph={5: 1.0, 15: 2.0, 30: 3.0, 60: 4.0},
        actual_mph={5: speed_mph, 15: speed_mph, 30: None, 60: None},
    )


def _state(*segments: SegmentForecast) -> CorridorState:
    return CorridorState(
        at=datetime(2024, 1, 20, 18, 0, tzinfo=UTC),
        step=100,
        horizon_minutes=(5, 15, 30, 60),
        segments=segments,
        model="GraphWaveNet · test",
        source="unit test",
    )


def _path(edge_ids: tuple[str, ...]) -> RoadPath:
    # Two points per edge, spaced so each edge is roughly 111 m long.
    geometry = tuple(
        GeoPoint(lat=13.06 + index * 0.001, lon=80.24) for index in range(len(edge_ids) + 1)
    )
    return RoadPath(
        nodes=tuple(str(index) for index in range(len(edge_ids) + 1)),
        edges=edge_ids,
        names=tuple("Test road" for _ in edge_ids),
        distance_m=111.0 * len(edge_ids),
        travel_s=13.0 * len(edge_ids),
        geometry=geometry,
    )


def test_way_of_splits_direction_and_part() -> None:
    assert way_of("186797304#2") == ("186797304", False)
    assert way_of("-28503517#1") == ("28503517", True)
    assert way_of("995146898") == ("995146898", False)


def test_horizon_follows_the_arrival_time() -> None:
    assert horizon_for_eta(120) == 5
    assert horizon_for_eta(900) == 15
    assert horizon_for_eta(1_500) == 30
    assert horizon_for_eta(4_000) == 60


def test_a_slow_forecast_costs_more_than_free_flow() -> None:
    fast = _state(_segment("111#0", 30.0))
    slow = _state(_segment("111#0", 6.0))
    path = _path(("111:0:+",))
    quick = price_path(path, fast, HORIZON)
    crawling = price_path(path, slow, HORIZON)
    assert crawling.travel_s > quick.travel_s * 3
    assert crawling.delay_s > 0
    assert quick.coverage == 1.0


def test_unmonitored_ways_fall_back_and_are_reported_as_uncovered() -> None:
    state = _state(_segment("111#0", 10.0))
    priced = price_path(_path(("111:0:+", "222:0:+")), state, HORIZON)
    assert 0.4 < priced.coverage < 0.6
    assert priced.slowest[0].segment_id == "111#0"


def test_a_blocked_way_is_penalised_even_when_the_forecast_is_clear() -> None:
    state = _state(_segment("111#0", 30.0))
    path = _path(("111:0:+",))
    open_road = price_path(path, state, HORIZON)
    blocked = price_path(path, state, HORIZON, frozenset({"111"}))
    assert blocked.travel_s > open_road.travel_s + 500


def test_direction_is_respected_when_both_directions_are_monitored() -> None:
    state = _state(_segment("111#0", 30.0), _segment("-111#0", 4.0))
    forward = price_path(_path(("111:0:+",)), state, HORIZON)
    reverse = price_path(_path(("111:0:-",)), state, HORIZON)
    assert reverse.travel_s > forward.travel_s * 3


def test_snapshot_reports_forecast_state_either_way() -> None:
    snapshot = DemoSimulation().snapshot()
    assert snapshot.forecast is not None
    assert snapshot.forecast.note
    if not snapshot.forecast.available:
        assert snapshot.forecast.horizon_minutes == 0
    else:
        assert snapshot.forecast.horizon_minutes in (5, 15, 30, 60)
        assert snapshot.forecast.segments_monitored > 0


def test_forecast_endpoints_degrade_without_a_checkpoint() -> None:
    client = TestClient(app)
    card = client.get("/api/forecast").json()
    assert "benchmarks" in card
    if not card["available"]:
        assert client.get("/api/forecast/segments").status_code == 503
        assert "detail" in card
    else:
        payload = client.get("/api/forecast/segments?horizon=15").json()
        assert payload["segments"]
        assert client.get("/api/forecast/segments?horizon=7").status_code == 422
        series = client.get(
            f"/api/forecast/series/{payload['segments'][0]['segment_id']}"
        ).json()
        assert len(series["forecast"]) == 12
