"""Forecast-weighted full-graph routing with explainable, connected replanning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import isfinite

from corridor import CorridorTimings, IntersectionController
from routing import SearchStats, should_switch_route
from schema import (
    ControlState,
    DecisionEvent,
    GeoPoint,
    PriorityRequest,
    RoadEstimate,
    RouteOption,
    RoutingDecision,
    SignalState,
)
from sim.chennai.roads import IncidentPlan, RoadNetwork, demo_plan

from .forecasting import ForecastBrain
from .traffic_source import SyntheticTrafficSource

SIGNAL_CHECKPOINTS = (("TL-F1", 0.3), ("TL-F2", 0.6), ("TL-F3", 0.9))


def road_names(path) -> list[str]:
    return list(dict.fromkeys(name or "Unnamed road" for name in path.names))


def route_label(path) -> str:
    names = road_names(path)
    for corridor in ("Avenue Road", "Valluvar Kottam High Road"):
        if corridor in names:
            return "Via " + corridor
    named = [name for name in names if name != "Unnamed road"]
    return "Via " + ", ".join(named[:2] or names[:1])


@dataclass
class DecisionOutput:
    roads: list[RoadEstimate]
    routes: list[RouteOption]
    signals: list[SignalState]
    decisions: list[DecisionEvent]
    vehicle_position: GeoPoint
    vehicle_heading_deg: float
    vehicle_speed_mps: float
    selected_route_id: str | None
    progress: float
    overall_confidence: float
    routing_decision: RoutingDecision
    active_sources: list[str] = field(default_factory=list)


class DecisionEngine:
    """Fresh A* search on each immutable forecast snapshot, never D* Lite.

    Preserve the currently traversed directed edge when replanning. Replan its
    suffix from the next graph node. If that edge closes, stop in place. Movement
    uses assumed static speeds and is an illustration, not measured vehicle data.
    """

    def __init__(
        self,
        network: RoadNetwork | None = None,
        plan: IncidentPlan | None = None,
    ) -> None:
        self.network = network or RoadNetwork()
        self.plan = plan or demo_plan()
        monitored = set(self.plan.normal.edges) | set(self.plan.bypass.edges)
        self.edge_lengths_m = {
            edge_id: self.network.edge_index[edge_id][1].length_m for edge_id in monitored
        }
        neighbours: dict[str, list[str]] = {edge_id: [] for edge_id in monitored}
        for path in (self.plan.normal, self.plan.bypass):
            for i, edge_id in enumerate(path.edges):
                neighbours[edge_id].extend(path.edges[max(0, i - 1) : i])
                neighbours[edge_id].extend(path.edges[i + 1 : i + 2])
        self.traffic = SyntheticTrafficSource(
            {
                edge_id: self.network.edge_index[edge_id][1].length_m
                / self.network.edge_index[edge_id][1].travel_s
                for edge_id in monitored
            },
            congested_edges=set(self.plan.blocked_edges) & monitored,
        )
        self.forecast = ForecastBrain(self.edge_lengths_m, neighbours, self.traffic)
        self._node = self.plan.normal.nodes[0]
        self._goal = self.plan.normal.nodes[-1]
        self._remaining = self.plan.normal.edges
        self._offset_m = 0.0
        self._distance_driven_m = 0.0
        self._last_time: float | None = None
        self._can_move = False
        self._history: list[DecisionEvent] = []
        self._last_action: tuple[str, str] | None = None
        self._controllers = {
            signal_id: IntersectionController(signal_id, CorridorTimings())
            for signal_id, _ in SIGNAL_CHECKPOINTS
        }

    def _road_estimates(self, now_s: float, controls: ControlState) -> dict[str, RoadEstimate]:
        recent = {
            edge_id: self.traffic.recent_speeds(edge_id, now_s, controls.congestion_scenario)
            for edge_id in self.edge_lengths_m
        }
        output = {}
        for edge_id in sorted(self.edge_lengths_m):
            estimate = self.forecast.estimate(
                edge_id,
                now_s,
                recent,
                historical_enabled=controls.historical_model_enabled,
                spatial_temporal_enabled=controls.spatial_temporal_model_enabled,
            )
            if estimate is not None:
                output[edge_id] = estimate
        return output

    def _advance(self, elapsed_s: float, blocked: frozenset[str]) -> None:
        if not self._can_move:
            return
        while self._remaining and elapsed_s > 0:
            edge_id = self._remaining[0]
            if edge_id in blocked:
                return
            edge = self.network.edge_index[edge_id][1]
            speed = edge.length_m / edge.travel_s
            distance = min(edge.length_m - self._offset_m, elapsed_s * speed)
            self._offset_m += distance
            self._distance_driven_m += distance
            elapsed_s -= distance / speed
            if self._offset_m >= edge.length_m - 1e-8:
                self._node = edge.target
                self._remaining = self._remaining[1:]
                self._offset_m = 0.0
            else:
                return

    def _position(self) -> GeoPoint:
        if not self._remaining or self._offset_m == 0:
            return self.network.nodes[self._node]
        edge = self.network.edge_index[self._remaining[0]][1]
        a, b = self.network.nodes[self._node], self.network.nodes[edge.target]
        fraction = self._offset_m / edge.length_m
        return GeoPoint(
            lat=a.lat + (b.lat - a.lat) * fraction, lon=a.lon + (b.lon - a.lon) * fraction
        )

    def tick(self, now_s: float, controls: ControlState) -> DecisionOutput:
        if not isfinite(now_s) or now_s < 0:
            raise ValueError("time must be finite and nonnegative")
        if self._last_time is not None and now_s < self._last_time:
            raise ValueError("mission time cannot move backwards")
        unknown = set(controls.blocked_edge_ids) - self.network.edge_index.keys()
        if unknown:
            raise ValueError("unknown closure edge ids")
        estimates = self._road_estimates(now_s, controls)
        blocked = (
            frozenset(controls.blocked_edge_ids)
            | (self.plan.blocked_edges if controls.accident else frozenset())
            | frozenset(edge_id for edge_id, estimate in estimates.items() if estimate.blockage)
        )
        if controls.no_path_scenario:
            blocked |= frozenset(
                edge_id
                for edge_id, (_, edge) in self.network.edge_index.items()
                if edge.target == self._goal
            )
        weights = {edge_id: item.travel_time_s for edge_id, item in estimates.items()}
        elapsed = 0.0 if self._last_time is None else now_s - self._last_time
        self._advance(elapsed, blocked)
        self._last_time = now_s
        position = self._position()
        current = self.network.path_from_edges(self._node, self._remaining, weights)
        current_valid = self.network.validate_path(current, blocked)
        prefix = self._remaining[:1] if self._offset_m > 0 else ()
        start = self.network.edge_index[prefix[0]][1].target if prefix else self._node
        stats = SearchStats()
        suffix = (
            None
            if set(prefix) & blocked
            else self.network.route(
                start,
                self._goal,
                blocked,
                algorithm="astar",
                weights=weights,
                departure_time_s=now_s,
                stats=stats,
            )
        )
        candidate = (
            None
            if suffix is None
            else self.network.path_from_edges(
                self._node,
                prefix + suffix.edges,
                weights,
            )
        )
        used_fraction = 0.0
        if prefix:
            edge = self.network.edge_index[prefix[0]][1]
            used_fraction = self._offset_m / edge.length_m
        used_time = (
            (weights.get(prefix[0], self.network.edge_index[prefix[0]][1].travel_s) * used_fraction)
            if prefix
            else 0.0
        )
        current_eta = max(0.0, current.travel_s - used_time) if current_valid else None
        candidate_eta = max(0.0, candidate.travel_s - used_time) if candidate else None
        sources = []
        if controls.historical_model_enabled:
            sources.append("historical baseline")
        if controls.spatial_temporal_model_enabled:
            sources.append("spatial-temporal baseline")
        weight_source = (
            "Fused synthetic baseline forecasts on monitored corridors; static elsewhere"
            if estimates
            else "Static fallback (no forecast available)"
        )
        status, action = "ok", "Retain current route"
        reason = "Current route is optimal under this weight snapshot."
        if blocked & set(self.plan.normal.edges):
            reason = (
                "The original mission corridor has a blocked segment. "
                "The current detour is optimal among connected open paths."
            )
        selected = current
        if candidate is None:
            status, action = "no_path", "Stop and report no open route"
            reason = (
                "The currently traversed segment is blocked. Vehicle remains in place."
                if set(prefix) & blocked
                else "No connected open road path reaches the hospital. Vehicle remains in place."
            )
            selected = None
        elif not candidate.edges:
            status, action, reason = "arrived", "Mission complete", "Hospital node reached."
            selected = candidate
        elif not current_valid or not self._can_move:
            action = "Reroute around closure" if not current_valid else "Select shortest route"
            reason = (
                "The previous route contains a blocked segment. Closure overrides hysteresis."
                if not current_valid
                else "A* found the minimum expected travel-time path."
            )
            selected = candidate
        elif candidate.edges != current.edges:
            if should_switch_route(current_eta, candidate_eta):
                action = "Reroute for predicted congestion"
                reason = (
                    f"Candidate saves {current_eta - candidate_eta:.1f}s and meets both "
                    "the 10s and 5% switching margins."
                )
                selected = candidate
            else:
                reason = (
                    f"Candidate saves {current_eta - candidate_eta:.1f}s. Retain the route "
                    "because both the 10s and 5% switching margins have not been met."
                )
        valid = selected is not None and self.network.validate_path(selected, blocked)
        self._can_move = valid and status == "ok"
        if selected is not None:
            self._remaining = selected.edges
        confidence = sum(item.confidence for item in estimates.values()) / max(1, len(estimates))
        decision = RoutingDecision(
            status=status,
            decision=action,
            reason=reason,
            weight_source=weight_source,
            original_roads=road_names(self.plan.normal),
            selected_roads=road_names(selected) if selected else [],
            blocked_edge_ids=sorted(blocked),
            current_eta_s=current_eta,
            candidate_eta_s=candidate_eta,
            saving_s=current_eta - candidate_eta
            if current_eta is not None and candidate_eta is not None
            else None,
            connectivity_valid=valid,
            nodes_explored=stats.nodes_explored,
            computation_ms=stats.computation_ms,
        )
        routes = []
        if selected is not None and selected.edges:
            routes.append(
                RouteOption(
                    route_id="route-active",
                    label=route_label(selected),
                    edge_ids=list(selected.edges),
                    eta_s=max(0.001, selected.travel_s - used_time),
                    selected=True,
                    reason=reason,
                    geometry=list(selected.geometry),
                )
            )
            if current.edges != selected.edges:
                routes.append(
                    RouteOption(
                        route_id="route-previous",
                        label="Previous route",
                        edge_ids=list(current.edges),
                        eta_s=max(0.001, current.travel_s - used_time),
                        selected=False,
                        reason="Blocked reference route"
                        if not current_valid
                        else "Previous route has higher expected travel time",
                        geometry=list(current.geometry),
                        feasible=current_valid,
                    )
                )
            if blocked & set(self.plan.normal.edges) and not any(
                route.edge_ids == list(self.plan.normal.edges) for route in routes
            ):
                routes.append(
                    RouteOption(
                        route_id="route-original",
                        label="Original mission route (blocked)",
                        edge_ids=list(self.plan.normal.edges),
                        eta_s=self.plan.normal.travel_s,
                        selected=False,
                        reason="Reference overlay of the original blocked corridor",
                        geometry=list(self.plan.normal.geometry),
                        feasible=False,
                    )
                )
        progress = self._distance_driven_m / max(
            1.0,
            self._distance_driven_m
            + (
                selected.distance_m - self._offset_m if selected is not None else current.distance_m
            ),
        )
        signals = []
        if self._can_move:
            signals = self._signal_states(now_s, routes[0].eta_s, confidence)
        else:
            # No new priority while stopped/arrived. Existing requests remain
            # subject to the FSM's expiration and maximum preemption timers.
            signals = [
                controller.update(now_s, None, ev_passed=status == "arrived")
                for controller in self._controllers.values()
            ]
        if (status, action) != self._last_action:
            self._history.append(
                DecisionEvent(at_s=now_s, kind=status, message=f"{action}: {reason}")
            )
            self._history = self._history[-20:]
            self._last_action = (status, action)
        events = list(self._history)
        heading = _bearing(position, selected.geometry[1]) if self._can_move else 0.0
        speed = 0.0
        if self._can_move and self._remaining:
            edge = self.network.edge_index[self._remaining[0]][1]
            speed = edge.length_m / edge.travel_s
        return DecisionOutput(
            roads=list(estimates.values()),
            routes=routes,
            signals=signals,
            decisions=events,
            vehicle_position=position,
            vehicle_heading_deg=heading,
            vehicle_speed_mps=speed,
            selected_route_id="route-active" if self._can_move else None,
            progress=min(1.0, progress),
            overall_confidence=confidence,
            routing_decision=decision,
            active_sources=sources,
        )

    def _signal_states(self, now_s, selected_eta, confidence) -> list[SignalState]:
        states = []
        for signal_id, fraction in SIGNAL_CHECKPOINTS:
            remaining = fraction * selected_eta
            request = None
            if remaining <= 45:
                request = PriorityRequest(
                    request_id=f"{signal_id}-{int(now_s)}",
                    vehicle_id="AMB-01",
                    intersection_id=signal_id,
                    approach_id="approach",
                    eta_s=remaining,
                    queue_pcu=4.0,
                    confidence=confidence,
                    expires_at=datetime.now(UTC) + timedelta(seconds=90),
                )
            states.append(self._controllers[signal_id].update(now_s, request))
        return states


def _bearing(a, b) -> float:
    from math import atan2, cos, degrees, radians, sin

    y = sin(radians(b.lon - a.lon)) * cos(radians(b.lat))
    x = cos(radians(a.lat)) * sin(radians(b.lat)) - sin(radians(a.lat)) * cos(radians(b.lat)) * cos(
        radians(b.lon - a.lon)
    )
    return (degrees(atan2(y, x)) + 360) % 360
