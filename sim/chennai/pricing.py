"""Price the demonstration road paths with the trained corridor forecast.

The routing graph in `roads.py` is built from the raw OpenStreetMap extract, while the
forecaster is trained on SUMO edges. Both keep the OSM way identifier, so a forecast
made for a monitored SUMO segment can be attributed back to the OSM edges of the same
way and direction. Ways with no monitored segment keep their assumed free-flow time,
and the share of the path that the model actually covers is reported rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

from forecast.runtime import CorridorState, SegmentForecast
from sim.chennai.roads import RoadPath, distance_m

MPH_TO_MPS = 0.44704
# The routing graph prices unmonitored roads at this assumed speed; see roads.py.
ASSUMED_SPEED_MPS = 8.5


@dataclass(frozen=True, slots=True)
class SlowSegment:
    segment_id: str
    name: str
    forecast_mph: float
    free_flow_mph: float
    delay_s: float


@dataclass(frozen=True, slots=True)
class PricedPath:
    travel_s: float
    free_flow_s: float
    closure_s: float
    distance_m: float
    covered_m: float
    horizon_minutes: int
    slowest: tuple[SlowSegment, ...]

    @property
    def coverage(self) -> float:
        return self.covered_m / self.distance_m if self.distance_m else 0.0

    @property
    def delay_s(self) -> float:
        """Delay the model predicts, excluding any camera-detected closure."""
        return max(0.0, self.travel_s - self.closure_s - self.free_flow_s)


def _way_and_direction(edge_id: str) -> tuple[str, bool]:
    """`roads.py` writes an edge as '<way>:<index>:<+|->'; '-' is the reverse direction."""
    parts = edge_id.split(":")
    if len(parts) != 3:
        return edge_id, False
    return parts[0], parts[2] == "-"


def _edge_lengths(path: RoadPath) -> list[float]:
    return [
        distance_m(a, b) for a, b in zip(path.geometry, path.geometry[1:], strict=False)
    ]


def price_path(
    path: RoadPath,
    state: CorridorState,
    horizon_minutes: int,
    blocked_ways: frozenset[str] = frozenset(),
    blocked_penalty_s: float = 900.0,
) -> PricedPath:
    """Sum forecast travel time over the path, falling back to the mapped free-flow time."""
    grouped = state.by_way()
    lengths = _edge_lengths(path)
    total = sum(lengths)
    travel = 0.0
    # Free flow is accumulated here rather than taken from the path's own assumed
    # speed, so the predicted delay compares like with like on every monitored edge.
    free_flow = 0.0
    covered = 0.0
    contributions: dict[str, SlowSegment] = {}

    closure = 0.0
    penalised: set[str] = set()

    for edge_id, length in zip(path.edges, lengths, strict=False):
        way, reverse = _way_and_direction(edge_id)
        assumed_s = length / ASSUMED_SPEED_MPS
        blocked = way in blocked_ways
        matches = [] if blocked else _matching(grouped.get(way, []), reverse)
        if not matches:
            # One closure costs the queue once, however many graph edges it spans.
            if blocked and way not in penalised:
                penalised.add(way)
                closure += blocked_penalty_s
            travel += assumed_s
            free_flow += assumed_s
            continue
        speed_mph = sum(m.speeds_mph[horizon_minutes] for m in matches) / len(matches)
        free_flow_mph = sum(m.free_flow_mph for m in matches) / len(matches)
        segment_time = length / max(speed_mph * MPH_TO_MPS, 0.5)
        segment_free_flow = length / max(free_flow_mph * MPH_TO_MPS, 0.5)
        travel += segment_time
        free_flow += segment_free_flow
        covered += length
        delay = segment_time - segment_free_flow
        best = matches[0]
        existing = contributions.get(best.segment_id)
        contributions[best.segment_id] = SlowSegment(
            segment_id=best.segment_id,
            name=best.name or f"Way {way}",
            forecast_mph=round(speed_mph, 1),
            free_flow_mph=round(free_flow_mph, 1),
            delay_s=round((existing.delay_s if existing else 0.0) + max(0.0, delay), 1),
        )

    slowest = tuple(
        sorted(contributions.values(), key=lambda item: -item.delay_s)[:5]
    )
    return PricedPath(
        travel_s=travel + closure,
        free_flow_s=free_flow,
        closure_s=closure,
        distance_m=total,
        covered_m=covered,
        horizon_minutes=horizon_minutes,
        slowest=slowest,
    )


def _matching(segments: list[SegmentForecast], reverse: bool) -> list[SegmentForecast]:
    matches = [
        segment for segment in segments if segment.segment_id.startswith("-") == reverse
    ]
    # A way is sometimes mapped in one direction only; using the other direction is a
    # better estimate than discarding the forecast entirely for that stretch of road.
    return matches or segments
