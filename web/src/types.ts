export type GeoPoint = { lat: number; lon: number };

export type RouteOption = {
  route_id: string;
  label: string;
  edge_ids: string[];
  eta_s: number;
  selected: boolean;
  reason: string;
  geometry: GeoPoint[];
  feasible: boolean;
};

export type RoutingDecision = {
  status: string;
  decision: string;
  reason: string;
  algorithm: string;
  weight_source: string;
  original_roads: string[];
  selected_roads: string[];
  blocked_edge_ids: string[];
  current_eta_s: number | null;
  candidate_eta_s: number | null;
  saving_s: number | null;
  minimum_saving_s: number;
  minimum_saving_fraction: number;
  connectivity_valid: boolean;
  nodes_explored: number;
  computation_ms: number;
};

export type SignalState = {
  intersection_id: string;
  phase: string;
  active_approach: string | null;
  seconds_to_change: number | null;
  conflicting_green: boolean;
};

export type Snapshot = {
  schema_version: string;
  generated_at: string;
  sim_time_s: number;
  scenario: string;
  vehicle: {
    vehicle_id: string;
    position: GeoPoint;
    speed_mps: number;
    heading_deg: number;
    route_id: string | null;
    progress: number;
  };
  roads: {
    road_id: string;
    travel_time_s: number;
    confidence: number;
    blockage: boolean;
    explanation: string;
  }[];
  routes: RouteOption[];
  signals: SignalState[];
  decisions: { at_s: number; kind: string; message: string }[];
  controls: {
    accident: boolean;
    macro_feed: boolean;
    camera: boolean;
    adoption_percent: number;
    historical_model_enabled: boolean;
    spatial_temporal_model_enabled: boolean;
    congestion_scenario: boolean;
    blocked_edge_ids: string[];
  };
  health: {
    macro_age_s: number;
    perception_online: boolean;
    overall_confidence: number;
  };
  forecast: {
    available: boolean;
    model: string;
    source: string;
    horizon_minutes: number;
    at: string | null;
    replay_step: number | null;
    route_coverage: number;
    corridor_mean_mph: number | null;
    segments_monitored: number;
    forecast_delay_s: number;
    slowest: {
      segment_id: string;
      name: string;
      forecast_mph: number;
      free_flow_mph: number;
      observed_mph: number;
      actual_mph: number | null;
    }[];
    note: string;
  } | null;
  incident: GeoPoint | null;
  routing_decision: RoutingDecision | null;
};

export type BaselineId = "B0" | "B1" | "B2" | "B3" | "B4" | "B5";

export type RunMetrics = {
  run_id: string;
  baseline: BaselineId;
  scenario: string;
  seed: number;
  ambulance_travel_time_s: number;
  stops: number;
  route_switches: number;
  green_on_arrival_rate: number;
  cross_traffic_added_delay_s: number;
  recovery_time_s: number;
  reroute_reaction_time_s: number | null;
  route_regret_s: number;
  conflicting_green_violations: number;
};

export type GhostReplay = {
  replay_id: string;
  scenario: string;
  seed: number;
  duration_s: number;
  routes: RouteOption[];
  frames: {
    t_s: number;
    event: string | null;
    vehicles: {
      baseline: BaselineId;
      position: GeoPoint;
      progress: number;
      arrived: boolean;
    }[];
  }[];
  metrics: RunMetrics[];
  origin: GeoPoint | null;
  hospital: GeoPoint | null;
  incident: GeoPoint | null;
  assumptions: string[];
  demonstration_only: boolean;
};

export type ResultsSummary = {
  generated_at: string;
  total_runs: number;
  scenarios: string[];
  baselines: {
    baseline: BaselineId;
    runs: number;
    mean_travel_time_s: number;
    ci95_travel_time_s: number;
    p95_travel_time_s: number;
    mean_green_on_arrival_rate: number;
    mean_cross_traffic_delay_s: number;
    mean_stops: number;
  }[];
};

export type HorizonMetrics = Record<string, { mae_mph: number; rmse_mph: number; mape_percent: number; count: number }>;

export type BenchmarkReport = {
  dataset: string;
  seed: number;
  model: HorizonMetrics;
  coverage_90: number | null;
  baselines: Record<string, HorizonMetrics>;
};

export type ForecastCard = {
  available: boolean;
  detail?: string;
  model?: string;
  dataset?: string;
  seed?: number;
  trained_on?: string;
  provenance?: string;
  segments?: number;
  input_minutes?: number;
  horizon_minutes?: number[];
  best_epoch?: number | null;
  best_validation_mae_mph?: number | null;
  trained_device?: string | null;
  torch_version?: string | null;
  git_revision?: string | null;
  test_report?: HorizonMetrics;
  test_baselines?: Record<string, HorizonMetrics>;
  coverage_90?: number | null;
  replay_split?: string;
  limitation?: string;
  benchmarks: BenchmarkReport[];
};

export type ForecastSegmentReading = {
  segment_id: string;
  osm_way_id: string;
  name: string;
  length_m: number;
  observed_mph: number;
  forecast_mph: number;
  stddev_mph: number;
  actual_mph: number | null;
  free_flow_mph: number;
  congestion: number;
  on_route: boolean;
};

export type ForecastSegments = {
  at: string;
  replay_step: number;
  model: string;
  source: string;
  horizon_minutes: number;
  segments: ForecastSegmentReading[];
};

export type ForecastSeries = {
  segment_id: string;
  name: string;
  free_flow_mph: number;
  history: { at: string; speed_mph: number }[];
  forecast: {
    at: string | null;
    horizon_minutes: number;
    forecast_mph: number;
    low_mph: number;
    high_mph: number;
    actual_mph: number | null;
  }[];
};
