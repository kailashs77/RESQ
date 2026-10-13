"""Confidence-aware road travel-time fusion."""

from dataclasses import dataclass
from math import sqrt

from schema import RoadEstimate, RoadObservation, SourceKind


@dataclass(frozen=True, slots=True)
class FusionConfig:
    blockage_confidence: float = 0.8
    near_closed_time_s: float = 3600.0
    macro_age_penalty_per_s: float = 0.02
    perception_distance_penalty_per_m: float = 0.004


def _adjusted_stddev(observation: RoadObservation, config: FusionConfig) -> float:
    if observation.source == SourceKind.MACRO:
        return observation.stddev_s * (1 + observation.age_s * config.macro_age_penalty_per_s)
    distance = observation.distance_m or 0
    confidence_penalty = 1 + (1 - observation.confidence)
    return observation.stddev_s * (
        1 + distance * config.perception_distance_penalty_per_m
    ) * confidence_penalty


def fuse_observations(
    road_id: str,
    *observations: RoadObservation | None,
    config: FusionConfig | None = None,
) -> RoadEstimate:
    """Fuse any number of independent observations with inverse-variance weighting.

    Any observation may be `None` (a source that failed, was disabled, or has no
    reading for this road) and is simply excluded. The brain therefore keeps
    producing an estimate as long as at least one source is present, and the
    estimate degrades gracefully to whichever subset of sources is alive.
    """

    config = config or FusionConfig()
    present = [item for item in observations if item is not None]
    if not present:
        raise ValueError("at least one observation is required")
    for observation in present:
        if observation.road_id != road_id:
            raise ValueError("all observations must refer to the requested road")

    blocked = [
        item
        for item in present
        if item.blockage and item.confidence >= config.blockage_confidence
    ]
    if blocked:
        override = max(blocked, key=lambda item: item.confidence)
        macro = next((item for item in present if item.source == SourceKind.MACRO), None)
        perception = next((item for item in present if item.source == SourceKind.PERCEPTION), None)
        return RoadEstimate(
            road_id=road_id,
            travel_time_s=config.near_closed_time_s,
            confidence=override.confidence,
            macro_time_s=macro.travel_time_s if macro else None,
            perception_time_s=perception.travel_time_s if perception else None,
            blockage=True,
            explanation=f"{override.source.value} override: high-confidence blockage",
        )

    adjusted = [(item, _adjusted_stddev(item, config)) for item in present]
    precision_sum = sum(1 / (stddev**2) for _, stddev in adjusted)
    travel_time = sum(item.travel_time_s / (stddev**2) for item, stddev in adjusted)
    travel_time /= precision_sum
    combined_stddev = sqrt(1 / precision_sum)
    confidence = max(0.0, min(1.0, 1 - combined_stddev / max(travel_time, 1)))
    source_label = " + ".join(sorted({item.source.value for item in present}))

    macro = next((item for item in present if item.source == SourceKind.MACRO), None)
    perception = next((item for item in present if item.source == SourceKind.PERCEPTION), None)

    return RoadEstimate(
        road_id=road_id,
        travel_time_s=travel_time,
        confidence=confidence,
        macro_time_s=macro.travel_time_s if macro else None,
        perception_time_s=perception.travel_time_s if perception else None,
        blockage=False,
        explanation=f"Inverse-variance estimate from {source_label}",
    )
