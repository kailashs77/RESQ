"""Version-one messages shared by simulation, decision services, and the UI."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceKind(StrEnum):
    MACRO = "macro"
    PERCEPTION = "perception"
    HISTORICAL = "historical"
    SPATIAL_TEMPORAL = "spatial_temporal"
    FUSED = "fused"


class SignalPhase(StrEnum):
    NORMAL = "normal"
    REQUEST_VALID = "request_valid"
    PRE_CLEAR = "pre_clear"
    SAFE_TRANSITION = "safe_transition"
    EV_SERVICE = "ev_service"
    EV_PASSED = "ev_passed"
    RECOVERY = "recovery"


class BaselineId(StrEnum):
    B0 = "B0"
    B1 = "B1"
    B2 = "B2"
    B3 = "B3"
    B4 = "B4"
    B5 = "B5"


class ScenarioName(StrEnum):
    OFF_PEAK = "off_peak"
    EVENING_PEAK = "evening_peak"
    SATURATED = "saturated"
    HIDDEN_ACCIDENT = "hidden_accident"
    STALE_FEED = "stale_feed"
    CAMERA_DEGRADED = "camera_degraded"


class GeoPoint(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class VehicleState(StrictModel):
    vehicle_id: str
    position: GeoPoint
    speed_mps: float = Field(ge=0)
    heading_deg: float = Field(ge=0, lt=360)
    route_id: str | None = None
    progress: float = Field(default=0, ge=0, le=1)


class RoadObservation(StrictModel):
    road_id: str
    source: SourceKind
    travel_time_s: float = Field(gt=0)
    stddev_s: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    age_s: float = Field(default=0, ge=0)
    distance_m: float | None = Field(default=None, ge=0)
    blockage: bool = False


class RoadEstimate(StrictModel):
    road_id: str
    travel_time_s: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    macro_time_s: float | None = Field(default=None, gt=0)
    perception_time_s: float | None = Field(default=None, gt=0)
    blockage: bool = False
    explanation: str


class PriorityRequest(StrictModel):
    request_id: str
    vehicle_id: str
    intersection_id: str
    approach_id: str
    eta_s: float = Field(ge=0)
    queue_pcu: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    expires_at: datetime


class SignalState(StrictModel):
    intersection_id: str
    phase: SignalPhase
    active_approach: str | None = None
    seconds_to_change: float | None = Field(default=None, ge=0)
    conflicting_green: bool = False


class RouteOption(StrictModel):
    route_id: str
    label: str
    edge_ids: list[str]
    eta_s: float = Field(gt=0)
    selected: bool = False
    reason: str
    geometry: list[GeoPoint]
    feasible: bool = True


class DecisionEvent(StrictModel):
    at_s: float = Field(ge=0)
    kind: str
    message: str


class RoutingDecision(StrictModel):
    status: str
    decision: str
    reason: str
    algorithm: str = "A* shortest path"
    weight_source: str
    original_roads: list[str] = Field(default_factory=list)
    selected_roads: list[str] = Field(default_factory=list)
    blocked_edge_ids: list[str] = Field(default_factory=list)
    current_eta_s: float | None = Field(default=None, ge=0)
    candidate_eta_s: float | None = Field(default=None, ge=0)
    saving_s: float | None = None
    minimum_saving_s: float = 10.0
    minimum_saving_fraction: float = 0.05
    connectivity_valid: bool = False
    nodes_explored: int = Field(default=0, ge=0)
    computation_ms: float = Field(default=0.0, ge=0)


class ControlState(StrictModel):
    accident: bool = False
    macro_feed: bool = True
    camera: bool = True
    adoption_percent: int = Field(default=10, ge=1, le=100)
    historical_model_enabled: bool = True
    spatial_temporal_model_enabled: bool = True
    congestion_scenario: bool = False
    blocked_edge_ids: list[str] = Field(default_factory=list)


class DataHealth(StrictModel):
    macro_age_s: float = Field(ge=0)
    perception_online: bool
    overall_confidence: float = Field(ge=0, le=1)


class NetworkSnapshot(StrictModel):
    schema_version: str = "1.0"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sim_time_s: float = Field(ge=0)
    scenario: str
    vehicle: VehicleState
    roads: list[RoadEstimate]
    routes: list[RouteOption]
    signals: list[SignalState]
    decisions: list[DecisionEvent]
    controls: ControlState
    health: DataHealth
    incident: GeoPoint | None = None
    routing_decision: RoutingDecision | None = None


class ExperimentConfig(StrictModel):
    baseline: BaselineId
    scenario: ScenarioName
    seed: int = Field(ge=0)
    adoption_percent: int = Field(default=10, ge=1, le=100)
    gps_noise_m: float = Field(default=10, ge=0)


class RunMetrics(StrictModel):
    run_id: str
    baseline: BaselineId
    scenario: ScenarioName
    seed: int
    ambulance_travel_time_s: float = Field(gt=0)
    stops: int = Field(ge=0)
    route_switches: int = Field(ge=0)
    green_on_arrival_rate: float = Field(ge=0, le=1)
    cross_traffic_added_delay_s: float = Field(ge=0)
    recovery_time_s: float = Field(ge=0)
    reroute_reaction_time_s: float | None = Field(default=None, ge=0)
    route_regret_s: float = Field(ge=0)
    conflicting_green_violations: int = Field(default=0, ge=0)


class ReplayVehicle(StrictModel):
    baseline: BaselineId
    position: GeoPoint
    progress: float = Field(ge=0, le=1)
    arrived: bool


class ReplayFrame(StrictModel):
    t_s: float = Field(ge=0)
    vehicles: list[ReplayVehicle]
    event: str | None = None


class GhostReplay(StrictModel):
    replay_id: str
    scenario: ScenarioName
    seed: int
    duration_s: float = Field(gt=0)
    routes: list[RouteOption]
    frames: list[ReplayFrame]
    metrics: list[RunMetrics]
    origin: GeoPoint | None = None
    hospital: GeoPoint | None = None
    incident: GeoPoint | None = None
    assumptions: list[str] = Field(default_factory=list)
    demonstration_only: bool = True


class BaselineSummary(StrictModel):
    baseline: BaselineId
    runs: int = Field(ge=0)
    mean_travel_time_s: float = Field(ge=0)
    ci95_travel_time_s: float = Field(ge=0)
    p95_travel_time_s: float = Field(ge=0)
    mean_green_on_arrival_rate: float = Field(ge=0, le=1)
    mean_cross_traffic_delay_s: float = Field(ge=0)
    mean_stops: float = Field(ge=0)


class ResultsSummary(StrictModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_runs: int = Field(ge=0)
    scenarios: list[ScenarioName]
    baselines: list[BaselineSummary]


class ExperimentRequest(StrictModel):
    baselines: list[BaselineId] = Field(default_factory=lambda: list(BaselineId))
    scenarios: list[ScenarioName] = Field(default_factory=lambda: list(ScenarioName))
    seeds: int = Field(default=5, ge=1, le=100)
    adoption_percent: int = Field(default=10, ge=1, le=100)
