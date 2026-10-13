"""Road-network-backed, deterministic review demonstration.

When a trained corridor checkpoint is present, the travel times behind the route
decision come from the Graph WaveNet forecast rather than from fixed staged numbers.
The staged incident stays staged: it is a scripted perception event used to show how
a high-confidence camera detection overrides a macro forecast that cannot yet see it.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from math import atan2, cos, degrees, radians, sin
from time import monotonic

from forecast.runtime import CorridorState, corridor_forecaster, horizon_for_eta
from fusion import fuse_observations
from routing import should_switch_route
from schema import (
    ControlState,
    DataHealth,
    DecisionEvent,
    ForecastSegment,
    ForecastSummary,
    NetworkSnapshot,
    RoadEstimate,
    RoadObservation,
    RouteOption,
    SignalPhase,
    SignalState,
    SourceKind,
    VehicleState,
)
from sim.chennai.pricing import PricedPath, price_path
from sim.chennai.roads import demo_plan, point_along

# Staged inputs, not empirical performance measurements.
INCIDENT_QUEUE_S = 120.0
RESQ_CUE_S = 8.0


def _heading(path, progress: float) -> float:
    current = point_along(path, progress)
    ahead = point_along(path, min(1.0, progress + 0.002))
    y = sin(radians(ahead.lon - current.lon)) * cos(radians(ahead.lat))
    x = cos(radians(current.lat)) * sin(radians(ahead.lat)) - sin(radians(current.lat)) * cos(
        radians(ahead.lat)
    ) * cos(radians(ahead.lon - current.lon))
    return (degrees(atan2(y, x)) + 360) % 360


def _blocked_ways(plan) -> frozenset[str]:
    return frozenset(edge_id.split(":")[0] for edge_id in plan.blocked_edges)


class DemoSimulation:
    def __init__(self) -> None:
        self.controls = ControlState()
        self._started_at = monotonic()

    def reset(self) -> None:
        self.controls = ControlState()
        self._started_at = monotonic()

    def update_controls(self, **changes: bool | int) -> ControlState:
        current = self.controls.model_dump()
        unknown = set(changes) - set(current)
        if unknown:
            raise ValueError(f"unknown controls: {', '.join(sorted(unknown))}")
        current.update(changes)
        self.controls = ControlState.model_validate(current)
        # Each control change starts a new staged run, avoiding a jump between roads.
        self._started_at = monotonic()
        return deepcopy(self.controls)

    # -------------------------------------------------------------- estimates

    def _staged_estimates(self) -> list[RoadEstimate]:
        """Fallback road estimates used before a corridor checkpoint exists."""
        adoption_penalty = max(0, 30 - self.controls.adoption_percent) / 10
        base = {"A1": 32.0, "A2": 38.0, "A3": 27.0, "B1": 39.0, "B2": 42.0, "B3": 37.0}
        estimates: list[RoadEstimate] = []
        for road_id, travel_time in base.items():
            # An offline feed retains a stale last-known observation. That is a
            # demo fallback; an empty sensor set must not crash the live socket.
            macro = RoadObservation(
                road_id=road_id,
                source=SourceKind.MACRO,
                travel_time_s=travel_time,
                stddev_s=3.0 + adoption_penalty,
                confidence=0.88 - adoption_penalty * 0.08,
                age_s=4.0 if self.controls.macro_feed else 180.0,
            )
            perception = None
            if road_id == "A2" and self.controls.camera:
                perception = RoadObservation(
                    road_id=road_id,
                    source=SourceKind.PERCEPTION,
                    travel_time_s=210.0 if self.controls.accident else 40.0,
                    stddev_s=5.0,
                    confidence=0.93,
                    distance_m=118.0,
                    blockage=self.controls.accident,
                )
            estimates.append(fuse_observations(road_id, macro, perception))
        return estimates

    def _forecast_estimates(
        self, state: CorridorState, horizon_minutes: int, blocked: frozenset[str]
    ) -> list[RoadEstimate]:
        """Fuse the model's macro forecast with the staged camera detection."""
        age_s = 4.0 if self.controls.macro_feed else 180.0
        accident_visible = self.controls.accident and self.controls.camera
        estimates: list[RoadEstimate] = []
        # Anything the camera can see is shown first, then the rest of the route, so
        # the interface never hides a detected blockage behind an arbitrary cut-off.
        candidates = [item for item in state.segments if item.on_route] or list(state.segments)
        detected = [item for item in candidates if item.osm_way_id in blocked]
        ordered = detected + [item for item in candidates if item not in detected]
        for segment in ordered[:8]:
            macro = RoadObservation(
                road_id=segment.segment_id,
                source=SourceKind.MACRO,
                travel_time_s=max(1.0, segment.travel_time_s(horizon_minutes)),
                stddev_s=max(
                    1.0,
                    segment.length_m
                    * max(segment.stddev_mph.get(horizon_minutes, 2.0) * 0.44704, 0.1)
                    / max(segment.speeds_mph.get(horizon_minutes, 1.0) * 0.44704, 0.5) ** 2,
                ),
                confidence=0.88 if self.controls.macro_feed else 0.55,
                age_s=age_s,
            )
            perception = None
            if accident_visible and segment.osm_way_id in blocked:
                perception = RoadObservation(
                    road_id=segment.segment_id,
                    source=SourceKind.PERCEPTION,
                    travel_time_s=max(1.0, segment.travel_time_s(horizon_minutes) * 5),
                    stddev_s=5.0,
                    confidence=0.93,
                    distance_m=118.0,
                    blockage=True,
                )
            estimates.append(fuse_observations(segment.segment_id, macro, perception))
        return estimates

    # --------------------------------------------------------------- snapshot

    def snapshot(self) -> NetworkSnapshot:
        plan = demo_plan()
        sim_time = monotonic() - self._started_at
        accident_visible = self.controls.accident and self.controls.camera
        blocked = _blocked_ways(plan)

        forecaster = corridor_forecaster()
        state: CorridorState | None = None
        priced_normal: PricedPath | None = None
        priced_bypass: PricedPath | None = None
        if forecaster is not None:
            try:
                state = forecaster.state()
                horizon = horizon_for_eta(plan.normal.travel_s)
                priced_normal = price_path(
                    plan.normal, state, horizon, blocked if accident_visible else frozenset()
                )
                priced_bypass = price_path(plan.bypass, state, horizon)
            except (RuntimeError, ValueError, KeyError, IndexError):
                state = None

        if state is not None and priced_normal is not None and priced_bypass is not None:
            normal_eta = priced_normal.travel_s
            bypass_eta = priced_bypass.travel_s + RESQ_CUE_S
            roads = self._forecast_estimates(state, priced_normal.horizon_minutes, blocked)
        else:
            normal_eta = plan.normal.travel_s + (INCIDENT_QUEUE_S if accident_visible else 0)
            bypass_eta = plan.bypass.travel_s + RESQ_CUE_S
            roads = self._staged_estimates()

        selected = "route-b" if should_switch_route(normal_eta, bypass_eta) else "route-a"
        path = plan.bypass if selected == "route-b" else plan.normal
        selected_eta = bypass_eta if selected == "route-b" else normal_eta
        progress = min(1.0, (sim_time % (selected_eta + 15)) / selected_eta)

        routes = [
            RouteOption(
                route_id="route-a",
                label="Regular · via Avenue Road",
                edge_ids=list(plan.normal.edges),
                eta_s=normal_eta,
                selected=selected == "route-a",
                reason=self._route_reason("route-a", priced_normal, accident_visible),
                geometry=list(plan.normal.geometry),
            ),
            RouteOption(
                route_id="route-b",
                label="ResQ · via Valluvar Kottam",
                edge_ids=list(plan.bypass.edges),
                eta_s=bypass_eta,
                selected=selected == "route-b",
                reason=self._route_reason("route-b", priced_bypass, accident_visible),
                geometry=list(plan.bypass.geometry),
            ),
        ]
        decisions = self._decisions(state, priced_normal, priced_bypass, selected)
        signals = [
            SignalState(
                intersection_id=signal_id,
                phase=SignalPhase.PRE_CLEAR if index == 0 else SignalPhase.REQUEST_VALID,
                active_approach="northbound" if selected == "route-b" else "eastbound",
                seconds_to_change=max(0, 4 - index),
                conflicting_green=False,
            )
            for index, signal_id in enumerate(("TL-04", "TL-07", "TL-11"))
        ]
        confidence = (0.91 if self.controls.camera else 0.58) - (
            0.18 if not self.controls.macro_feed else 0
        )
        return NetworkSnapshot(
            generated_at=datetime.now(UTC),
            sim_time_s=sim_time,
            scenario="staged-hidden-incident",
            vehicle=VehicleState(
                vehicle_id="AMB-01",
                position=point_along(path, progress),
                speed_mps=path.distance_m / path.travel_s if progress < 1 else 0,
                heading_deg=_heading(path, progress),
                route_id=selected,
                progress=progress,
            ),
            roads=roads,
            routes=routes,
            signals=signals,
            decisions=decisions,
            controls=self.controls,
            health=DataHealth(
                macro_age_s=4.0 if self.controls.macro_feed else 180.0,
                perception_online=self.controls.camera,
                overall_confidence=max(0, confidence),
            ),
            forecast=self._forecast_summary(state, priced_normal),
            incident=plan.incident,
        )

    # ------------------------------------------------------------ explanations

    def _route_reason(
        self, route_id: str, priced: PricedPath | None, accident_visible: bool
    ) -> str:
        if priced is None:
            if route_id == "route-a":
                return (
                    "Shortest mapped route; staged queue is hidden from the regular ambulance"
                    if self.controls.accident
                    else "Shortest route on mapped drivable roads"
                )
            return "Road-graph bypass of the staged incident; 8 s detection/decision allowance"
        share = f"{priced.coverage * 100:.0f}% of the route is monitored"
        if route_id == "route-a":
            if accident_visible:
                return f"Forecast plus camera blockage on the staged section; {share}"
            return (
                f"Priced with the {priced.horizon_minutes}-minute speed forecast; "
                f"{priced.delay_s:.0f} s of predicted delay, {share}"
            )
        return (
            f"Forecast-priced bypass at the {priced.horizon_minutes}-minute horizon; "
            f"{share}, plus an 8 s detection/decision allowance"
        )

    def _decisions(
        self,
        state: CorridorState | None,
        normal: PricedPath | None,
        bypass: PricedPath | None,
        selected: str,
    ) -> list[DecisionEvent]:
        events = [
            DecisionEvent(
                at_s=0,
                kind="route",
                message="Shortest drivable route selected from the local OSM graph",
            )
        ]
        if state is not None and normal is not None:
            events.append(
                DecisionEvent(
                    at_s=0,
                    kind="forecast",
                    message=(
                        f"{state.model} predicts {normal.horizon_minutes} min ahead: "
                        f"{normal.delay_s:.0f} s of traffic delay on the regular route"
                    ),
                )
            )
            if normal.slowest:
                worst = normal.slowest[0]
                events.append(
                    DecisionEvent(
                        at_s=0,
                        kind="forecast",
                        message=(
                            f"Slowest predicted link {worst.name or worst.segment_id}: "
                            f"{worst.forecast_mph:.0f} mph against "
                            f"{worst.free_flow_mph:.0f} mph free flow"
                        ),
                    )
                )
        if self.controls.accident:
            accident_visible = self.controls.camera
            events.append(
                DecisionEvent(
                    at_s=0,
                    kind="reroute" if accident_visible else "warning",
                    message=(
                        "Camera blockage overrides the macro forecast; "
                        "ResQ takes the connected bypass"
                        if accident_visible
                        else "Incident is hidden while camera input is unavailable"
                    ),
                )
            )
        if state is not None and bypass is not None and normal is not None:
            saving = normal.travel_s - bypass.travel_s
            comparison = (
                f"saves {saving:.0f} s" if saving > 0 else f"costs {-saving:.0f} s more"
            )
            events.append(
                DecisionEvent(
                    at_s=0,
                    kind="decision",
                    message=(
                        f"{'Bypass taken' if selected == 'route-b' else 'Regular route kept'}: "
                        f"the predicted bypass {comparison}"
                    ),
                )
            )
        return events

    def _forecast_summary(
        self, state: CorridorState | None, priced: PricedPath | None
    ) -> ForecastSummary:
        if state is None or priced is None:
            return ForecastSummary(
                available=False,
                model="rule-based baseline",
                source="staged travel times",
                horizon_minutes=0,
                note=(
                    "No corridor checkpoint is loaded. Build the simulated corridor dataset "
                    "and train CHENNAI-SIM to drive the route decision with the model."
                ),
            )
        monitored = [segment for segment in state.segments if segment.on_route]
        reference = monitored or list(state.segments)
        mean_speed = sum(
            segment.speeds_mph[priced.horizon_minutes] for segment in reference
        ) / max(len(reference), 1)
        return ForecastSummary(
            available=True,
            model=state.model,
            source=state.source,
            horizon_minutes=priced.horizon_minutes,
            at=state.at,
            replay_step=state.step,
            route_coverage=min(1.0, priced.coverage),
            corridor_mean_mph=round(mean_speed, 2),
            segments_monitored=len(state.segments),
            forecast_delay_s=round(priced.delay_s, 1),
            slowest=[
                ForecastSegment(
                    segment_id=item.segment_id,
                    osm_way_id=item.segment_id.lstrip("-").split("#")[0],
                    name=item.name,
                    length_m=max(1.0, self._segment_length(state, item.segment_id)),
                    observed_mph=self._segment_observed(state, item.segment_id),
                    forecast_mph=item.forecast_mph,
                    stddev_mph=self._segment_band(state, item.segment_id, priced.horizon_minutes),
                    free_flow_mph=max(1.0, item.free_flow_mph),
                    actual_mph=self._segment_actual(
                        state, item.segment_id, priced.horizon_minutes
                    ),
                    on_route=True,
                )
                for item in priced.slowest
            ],
            note=(
                "Speeds come from a checkpoint trained on simulated corridor histories; "
                "they describe the SUMO study area, not measured Chennai traffic."
            ),
        )

    @staticmethod
    def _find(state: CorridorState, segment_id: str):
        return next(
            (item for item in state.segments if item.segment_id == segment_id), None
        )

    def _segment_length(self, state: CorridorState, segment_id: str) -> float:
        segment = self._find(state, segment_id)
        return segment.length_m if segment else 1.0

    def _segment_observed(self, state: CorridorState, segment_id: str) -> float:
        segment = self._find(state, segment_id)
        return max(0.0, segment.observed_mph) if segment else 0.0

    def _segment_band(self, state: CorridorState, segment_id: str, horizon: int) -> float:
        segment = self._find(state, segment_id)
        return max(0.0, segment.stddev_mph.get(horizon, 0.0)) if segment else 0.0

    def _segment_actual(
        self, state: CorridorState, segment_id: str, horizon: int
    ) -> float | None:
        segment = self._find(state, segment_id)
        return segment.actual_mph.get(horizon) if segment else None
