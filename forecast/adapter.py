"""Convert a calibrated speed forecast into the ResQ macro observation contract."""

from __future__ import annotations

from schema import RoadObservation, SourceKind

MPH_TO_MPS = 0.44704


def speed_forecast_to_observation(
    road_id: str,
    length_m: float,
    speed_mph: float,
    stddev_mph: float,
    age_s: float = 0,
) -> RoadObservation:
    if length_m <= 0 or stddev_mph < 0 or age_s < 0:
        raise ValueError("length must be positive; uncertainty and age cannot be negative")
    speed_mps = max(speed_mph * MPH_TO_MPS, 0.5)
    stddev_mps = max(stddev_mph * MPH_TO_MPS, 0.1)
    travel_time_s = length_m / speed_mps
    stddev_s = max(1.0, length_m * stddev_mps / speed_mps**2)
    confidence = max(0.0, min(1.0, 1 - stddev_s / max(travel_time_s, 1.0)))
    return RoadObservation(
        road_id=road_id,
        source=SourceKind.MACRO,
        travel_time_s=travel_time_s,
        stddev_s=stddev_s,
        confidence=confidence,
        age_s=age_s,
    )
