// Types mirror the REAL FastAPI responses of backend/backend/main.py (verified against the running API).
// Nothing here is invented: optional fields are those the backend omits in some modes.

export type TrafficSeverity = 'low' | 'moderate' | 'high' | 'severe';
export type CongestionLabel = 'Low' | 'Medium' | 'High';

// ---- GET /health ---------------------------------------------------------
export interface DataSourceStatus {
  status: string;
  source: string;
  is_live: boolean;
  is_real_road_geometry?: boolean;
  osmnx_installed?: boolean;
}
export interface HealthResponse {
  status: 'ok' | 'degraded';
  service: string;
  version: string;
  classifier_available: boolean;
  forecast_artifacts_available: boolean;
  road_network_loaded: boolean;
  openai_enabled: boolean;
  tomtom_enabled: boolean;
  data_sources: Record<'traffic' | 'road_network' | 'weather' | 'tomtom' | 'copilot', DataSourceStatus> & {
    /** Configuration state of the selected live-traffic provider (no external request is made by /health). */
    traffic_provider?: DataSourceStatus & TrafficProviderStatus;
  };
  notice: string;
}

// ---- GET /locations, /traffic/current -----------------------------------
export interface LocationsResponse {
  locations: string[];
  source: string;
  is_live: boolean;
}
export interface CurrentLocationRow {
  location: string;
  vehicle_count: number;
  avg_speed: number;
  rainfall: number;
  clouds: number;
  temperature: number;
  congestion_label: CongestionLabel;
}
export interface CurrentTrafficResponse {
  status: string;
  source: string;
  is_live: boolean;
  data_timestamp?: string;
  notice?: string;
  locations: CurrentLocationRow[];
}

// ---- POST /traffic/predict/{location}, GET /traffic/predictions ----------
export interface PredictionRequest {
  prediction_timestamp?: string | null;
  use_open_meteo_weather?: boolean;
  allow_simulated_weather_fallback?: boolean;
  vehicle_count?: number | null;
  avg_speed?: number | null;
  rainfall?: number | null;
  clouds?: number | null;
  temperature?: number | null;
}
export interface PredictionResult {
  location: string;
  origin_timestamp: string;
  prediction_timestamp: string;
  prediction_horizon_minutes: number;
  predicted_class_id: number;
  predicted_congestion: CongestionLabel;
  class_probabilities: Record<CongestionLabel, number> | null;
  input_measurements: {
    lag_vehicle_count: number;
    lag_avg_speed: number;
    lag_rainfall: number | null;
    lag_clouds: number | null;
    lag_temperature: number | null;
  };
  model_name: string;
  source: string;
  is_live: boolean;
  weather_input_source: string;
  notice: string;
}
/** Per-location failure entry that /traffic/predictions can contain instead of a prediction. */
export interface PredictionError {
  location: string;
  status: 'error';
  error: string;
  is_live: boolean;
}
export interface AllPredictionsResponse {
  predictions: (PredictionResult | PredictionError)[];
  source: string;
  is_live: boolean;
}

// ---- GET /forecasts/{location} ------------------------------------------
export type RegressorFamily = 'random_forest' | 'xgboost';
export interface ForecastResult {
  location: string;
  origin_timestamp: string;
  target_timestamp: string;
  horizon_minutes: 30 | 60;
  source: string;
  is_live: boolean;
  notice: string;
  vehicle_count: number | null;
  avg_speed: number | null;
  predicted_congestion: CongestionLabel | null;
  congestion_probabilities: Record<CongestionLabel, number> | null;
  forecast_model: string;
  regressor_model: RegressorFamily;
}

// ---- Routing -------------------------------------------------------------
/** Node from GET /routing/nodes. NOTE: backend uses latitude/longitude (NOT lat/lon). */
export interface RoutingNode {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  /** Human-readable label built from OSM road names (the id stays the routing key). */
  label?: string;
  road_names?: string[];
  label_source?: string;
}
export interface RoutingNodesResponse {
  graph_source: string;
  is_real_road_geometry: boolean;
  nodes: RoutingNode[];
}
export interface RouteSegment {
  from_node: string;
  to_node: string;
  length_m: number;
  estimated_speed_kph: number;
  congestion: string;
  travel_time_min: number;
  incident_ids: string[];
  highway: string;
  road_name: string | null;
  oneway: boolean;
  traffic_basis: string;
  measured_speed_kph: number | null;
}
export interface RouteStep {
  road: string;
  highway: string;
  distance_m: number;
  time_min: number;
  congestion: string;
  oneway: boolean;
}
export interface CalculatedRoute {
  rank: number;
  algorithm: string;
  node_sequence: string[];
  distance_km: number;
  estimated_travel_time_min: number;
  free_flow_estimate_min: number;
  delay_vs_free_flow_min: number;
  via_roads: string[];
  steps: RouteStep[];
  segments: RouteSegment[];
  measured_segment_count: number;
  measured_distance_share: number;
  polyline_latlon: [number, number][];
  graph_source: string;
  is_real_road_geometry: boolean;
}
export interface RouteCalculationResponse {
  origin_node: string;
  destination_node: string;
  algorithm: string;
  graph_source: string;
  route_count: number;
  routes: CalculatedRoute[];
  active_incident_count: number;
  origin_input: string;
  destination_input: string;
  departure_time: string;
  departure_time_source: string;
  estimated_arrival_time: string | null;
  traffic_cost_mode: string;
  notice: string;
  traffic_flow?: RouteTrafficFlowInfo;
  /** Provider-side ETA (traffic vs free-flow) for the same endpoints; reference only. Present when use_live_traffic was requested. */
  provider_eta?: ProviderEta;
}

/** Exactly ONE endpoint pair may be supplied (backend validator). */
export type RouteEndpoints =
  | { origin_node: string; destination_node: string }
  | { origin_lat: number; origin_lon: number; destination_lat: number; destination_lon: number }
  | { origin_name: string; destination_name: string };
export type RoutingAlgorithm = 'dijkstra' | 'astar';
export type RouteCalculationRequest = RouteEndpoints & {
  algorithm: RoutingAlgorithm;
  alternatives: number;
  rainfall_mm: number;
  use_simulated_congestion: boolean;
  use_tomtom_flow?: boolean;
  /** Use the backend-selected provider (HERE by default): measured flow on matched edges + provider ETA. */
  use_live_traffic?: boolean;
  departure_time?: string | null;
};

// ---- Incidents: manual / simulation / tomtom (live provider) --------------
export type IncidentKind = 'accident' | 'road_closure' | 'road_work' | 'obstruction' | 'hazard' | 'traffic_jam';
export type IncidentSource = 'manual' | 'simulation' | 'tomtom';
export interface IncidentCreate {
  edge_u: string;
  edge_v: string;
  kind: IncidentKind;
  severity: number; // 0..1
  source: 'manual' | 'simulation'; // 'tomtom' can only come from the provider feed
  expires_in_seconds: number;
  bidirectional: boolean;
  description: string;
}
/** Legacy edge-level record: what the routing engine prices (a TomTom incident yields one per matched directed edge). */
export interface IncidentRecord {
  id: string;
  edge_u: string;
  edge_v: string;
  kind: IncidentKind;
  severity: number;
  source: IncidentSource;
  created_at: string;
  expires_at: string;
  bidirectional: boolean;
  description: string;
  provider_id?: string | null;
}

/** State of the TomTom incident service, exactly as the backend reports it. */
export type IncidentProviderState = 'live' | 'live_empty' | 'cached' | 'disabled_no_key' | 'disabled' | 'unavailable' | 'idle';
export interface IncidentProviderError { type: string; message: string; http_status?: number | null; at?: string | null }
export interface IncidentProviderStatus {
  provider: string;
  state: IncidentProviderState;
  message: string;
  enabled: boolean;
  configured: boolean;
  is_live: boolean;
  using_cached_data: boolean;
  stale: boolean;
  incident_count: number;
  last_attempt_at: string | null;
  last_success_at: string | null;
  data_age_seconds: number | null;
  last_error: IncidentProviderError | null;
  consecutive_failures: number;
  poll_interval_seconds: number;
  min_refresh_seconds: number;
  stale_after_seconds: number;
  next_allowed_refresh_at: string | null;
  bbox: number[];
  attribution: string;
}
export interface IncidentCounts {
  manual: number; simulation: number; tomtom: number;
  tomtom_matched: number; tomtom_unmatched: number; tomtom_applied_to_routing: number; total: number;
}
export type FeedStatus = 'applied' | 'matched_not_applied' | 'unmatched' | 'stale';
export interface FeedEdge { u: string; v: string; key?: string | null; road_name?: string | null; length_m?: number | null }
export interface IncidentGeometry { type: 'Point' | 'LineString'; coordinates: number[] | number[][] } // GeoJSON order: [lon, lat]
export interface IncidentMatch { status: string; confidence: number; reason: string | null; reason_text: string | null; direction_used: boolean }
/** One incident as a user sees it. For TomTom this is ONE provider incident (matched to 0..n directed edges). */
export interface FeedIncident {
  id: string;
  source: IncidentSource;
  kind: IncidentKind;
  description: string;
  severity: number | null;
  severity_source: string | null;
  status: FeedStatus;
  matched: boolean;
  routing_applied: boolean;
  routing_skip_reason?: string | null;
  confirmed: boolean;
  stale: boolean;
  start_time: string | null;
  end_time: string | null;
  expires_at: string | null;
  created_at: string | null;
  last_seen_at: string | null;
  provider_id: string | null;
  edges: FeedEdge[];
  bidirectional: boolean;
  geometry: IncidentGeometry | null;
  latitude: number | null;
  longitude: number | null;
  match: IncidentMatch | null;
  /** Added by the shared engine (backend routing/engine.py); absent in older responses. */
  road_name?: string | null;
  road_name_source?: string | null;
  location_label?: string | null;
  impact_class?: ImpactClass | null;
  routing_effect?: 'block' | 'penalty' | 'none' | null;
  impact_score?: number | null;
  explanation?: string | null;
  unmatched_reason?: string | null;
  matched_length_m?: number | null;
  provider_length_m?: number | null;
  provider_delay_s?: number | null;
  blocked_lanes?: number | null;
  provider: {
    category?: string; magnitude_label?: string | null; delay_seconds?: number | null; length_m?: number | null;
    from?: string | null; to?: string | null; road_numbers?: string[]; probability_of_occurrence?: string | null;
  } | null;
}
export type ImpactClass = 'confirmed_closure' | 'unconfirmed_closure' | 'major_accident' | 'moderate_accident' | 'minor_accident'
  | 'construction' | 'lane_restriction' | 'obstruction' | 'traffic_jam' | 'hazard';
export interface IncidentsResponse {
  incidents: IncidentRecord[];
  source_types: string[];
  feed: FeedIncident[];
  counts: IncidentCounts;
  provider: IncidentProviderStatus;
  data_notice: string;
}
export interface IncidentRefreshResponse {
  refreshed: boolean; // a successful TomTom request completed in this call
  throttled: boolean; // no request was made (rate limit / back-off / disabled)
  provider: IncidentProviderStatus;
  counts: IncidentCounts;
  feed: FeedIncident[];
}
export interface IncidentImpactResponse {
  rerouted: boolean;
  reason: string;
  without_live_incidents: RouteCalculationResponse;
  with_incidents: Partial<RouteCalculationResponse> & { error?: string };
  incidents_on_original_route: FeedIncident[];
  incidents_on_new_route: FeedIncident[];
  delta: { travel_time_min: number | null; distance_km: number | null };
  provider: IncidentProviderStatus;
  /** Same engine as the Smart Route Planner (absent only if that call failed server-side). */
  plan?: Partial<RoutePlanResponse> | null;
}

// ---- GET /weather/current -------------------------------------------------
export interface WeatherResponse {
  status: 'ok' | 'stale_cache' | 'simulated_fallback' | 'unavailable';
  source: string;
  is_live: boolean;
  is_simulated: boolean;
  fetched_at?: string;
  observation_time?: string | null;
  timezone?: string;
  temperature_c?: number | null;
  relative_humidity_pct?: number | null;
  precipitation_mm?: number | null;
  rain_mm?: number | null;
  showers_mm?: number | null;
  weather_code?: number | null;
  cloud_cover_pct?: number | null;
  wind_speed_kmh?: number | null;
  wind_direction_deg?: number | null;
  wind_gusts_kmh?: number | null;
  cache_status?: string;
  cache_age_seconds?: number;
  last_error?: string;
  attribution?: string;
  notice?: string;
  error?: string;
  message?: string;
}

// ---- Live traffic provider (HERE / TomTom) ---------------------------------
/** The only labels traffic data may carry. `stale_cache` is explicitly NOT live. */
export type TrafficDataState = 'live' | 'cached' | 'stale_cache' | 'unavailable' | 'disabled';
export type TrafficLevel = 'free' | 'slow' | 'heavy' | 'severe' | 'closed';
export interface TrafficProviderStatus {
  provider: string;
  configured: boolean;
  state: string;
  message: string;
  last_success_at?: number | null;
  last_error?: string | null;
  /** True only after a real provider request returned segments for the Bhubaneswar window in this backend process. */
  coverage_observed: boolean;
  coverage_note: string;
  supports: { flow: boolean; tiles: boolean; route_eta: boolean };
}
export interface TrafficFlowSegment {
  path: [number, number][];
  current_kph: number | null;
  free_flow_kph: number | null;
  jam_factor: number | null;
  level: TrafficLevel;
  confidence: number | null;
  description: string | null;
}
export interface TrafficFlowResponse {
  provider: string;
  status: string;
  data_state: TrafficDataState;
  is_live: boolean;
  message: string | null;
  bbox: number[] | null;
  fetched_at: number | null;
  age_seconds: number | null;
  source_updated: string | null;
  segment_count: number;
  measurement_count: number;
  segments?: TrafficFlowSegment[];
}
export interface ProviderEta {
  provider: string;
  status: string;
  data_state: TrafficDataState;
  is_live: boolean;
  message?: string | null;
  duration_min?: number | null;
  free_flow_duration_min?: number | null;
  traffic_delay_min?: number | null;
  length_km?: number | null;
  age_seconds?: number | null;
  reference_only: boolean;
  note?: string;
}
export interface RouteTrafficFlowInfo {
  status?: string;
  provider?: string;
  is_live?: boolean;
  traffic_data_state?: TrafficDataState;
  age_seconds?: number | null;
  edges_matched?: number;
  segments_received?: number;
  coverage_best_route?: number;
  baseline_best_time_min?: number;
  baseline_best_nodes?: string[];
  note?: string;
  [k: string]: unknown;
}

// ---- GET /traffic/tomtom/flow -------------------------------------------
export interface TomTomFlowResponse {
  status: string;
  source: string;
  is_live: boolean;
  message?: string;
  [k: string]: unknown;
}

// ---- POST /copilot/chat ----------------------------------------------------

export interface CopilotResponse {
  answer: string;
  tool_results: Record<string, unknown>;
  mode:
    | 'rule_based'
    | 'ollama_tool_calling'
    | 'openai_function_calling'
    | 'rule_based_after_ollama_error'
    | 'rule_based_after_openai_error'
    | 'rule_based_after_llm_error'
    | 'validation'
    | string;
  model?: string;
  llm_error?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  mode?: CopilotResponse['mode'];
  toolResults?: Record<string, unknown>;
  isError?: boolean;
}

// ---- GET /models/evaluation (read-only view of saved training artifacts) ----
export interface ClassMetrics {
  accuracy: number;
  precision_macro: number;
  recall_macro: number;
  f1_macro: number;
  f1_weighted: number;
  classification_report: Record<string, { precision: number; recall: number; 'f1-score': number; support: number }>;
  confusion_matrix: number[][];
}
export interface RegressionMetrics { mae: number; rmse: number; smape_pct: number; n: number }
export interface ForecastHorizonBlock {
  split: Record<string, number | string>;
  targets: Record<'vehicle_count' | 'avg_speed', Record<string, RegressionMetrics>>;
  congestion_classification?: {
    model: string; accuracy: number; precision_macro: number; recall_macro: number; f1_macro: number;
    classification_report: ClassMetrics['classification_report']; confusion_matrix: number[][];
  };
}
export interface ModelEvaluationResponse {
  source: string;
  is_live: boolean;
  dataset_domain: string;
  classifier_metadata: {
    selected_model: string;
    model_type: string;
    classes: Record<string, string>;
    split_strategy?: string;
    train_rows?: number; validation_rows?: number; test_rows?: number;
    test_start?: string; test_end?: string;
    runtime_versions?: Record<string, string>;
  } | null;
  classification: {
    holdout_test_metrics: Record<string, ClassMetrics>;
    validation_metrics_for_model_selection: Record<string, ClassMetrics>;
  } | null;
  forecasting: {
    metadata: { split?: string; runtime_versions?: Record<string, string>; sarima_status?: string; lstm_status?: string };
    results: Record<'30m' | '60m', ForecastHorizonBlock>;
  } | null;
  not_available: string[];
  notice: string;
}

// ---- Road network, location search and incident-aware planning (POST /routes/plan) -----------------
export interface NetworkStatus {
  state: 'ready' | 'unavailable' | 'not_openstreetmap';
  source: string;
  is_real_osm: boolean;
  has_cache: boolean;
  nodes: number;
  edges: number;
  named_edges?: number;
  named_edge_share?: number;
  cache_path: string;
  cache_size_mb: number;
  last_error: string | null;
  error: { code: string; message: string; reason: string; hint: string } | null;
}
export interface NodeLabel {
  id: string; label: string; road_names: string[]; label_source: string; locality: string | null; latitude: number; longitude: number;
  nearest_road_distance_m?: number;
}
export interface GeocodeInfo {
  source: 'road_graph_name' | 'nominatim'; display_name: string; query: string; query_used: string; match_quality: number;
  attempts: { step: string; query?: string; outcome?: string; error?: string }[];
}
export interface PlaceMatch {
  input: string; node_id: string; snap_distance_m: number; latitude: number; longitude: number; road_label: NodeLabel; geocode: GeocodeInfo | null;
}
export interface RoadSegment { name: string | null; highway: string; positions: [number, number][] }
export interface RoadsResponse {
  graph_source: string; is_real_road_geometry: boolean; level: 'major' | 'all'; count: number; total_matching: number; truncated: boolean; segments: RoadSegment[];
}
export interface AnalysedIncident {
  id: string; source: IncidentSource; kind: IncidentKind; category: string | null; description: string;
  severity: number; severity_source: string | null; magnitude_label: string | null;
  status: FeedStatus; matched: boolean; routing_applied: boolean; unmatched_reason: string | null; match_confidence: number | null;
  confirmed: boolean; stale: boolean; start_time: string | null; end_time: string | null; last_report_time: string | null; last_seen_at: string | null;
  provider_length_m: number | null; provider_delay_s: number | null; matched_length_m: number; blocked_lanes: number | null;
  latitude: number | null; longitude: number | null; geometry: IncidentGeometry | null; edges: FeedEdge[];
  road_name: string | null; road_name_source: string | null; provider_from_to: string | null; location_label: string;
  impact_class: ImpactClass; routing_effect: 'block' | 'penalty' | 'none'; impact_score: number; explanation: string;
  affects_original_route?: boolean; affected_length_on_original_m?: number; affects_recommended_route?: boolean; on_alternative_routes?: string[];
}
export interface PlannedRoute extends CalculatedRoute {
  route_id: string; role?: string; incident_ids: string[]; incident_count: number;
  blocked_by_closure?: boolean; time_with_incidents_min?: number | null; free_flow_time_min?: number; is_original_route?: boolean;
}
export interface RouteDecision {
  action: 'keep_original' | 'reroute' | 'no_route'; reason: string; triggering_incidents?: string[];
  thresholds: { min_saving_s: number; min_saving_pct: number };
}
export interface RouteComparison {
  distance_delta_km: number; time_delta_vs_original_free_flow_min: number; time_delta_vs_original_with_incidents_min: number | null;
  incidents_avoided: string[]; incidents_still_on_recommended: string[]; original_is_blocked: boolean;
}
export interface RoutePlanResponse {
  algorithm: RoutingAlgorithm; graph_source: string; decision: RouteDecision;
  original_route: PlannedRoute; recommended_route: PlannedRoute | null; alternative_routes: PlannedRoute[];
  comparison: RouteComparison | null; incident_analysis: AnalysedIncident[];
  origin: PlaceMatch; destination: PlaceMatch; network: NetworkStatus;
  incident_data: { provider: IncidentProviderStatus; refresh: unknown; counts: IncidentCounts; live_data_supported: boolean; incident_free_claim_supported: boolean; note: string };
  data_sources: Record<string, string>; departure_time: string; notice: string;
}
export type RoutePlanRequest = RouteCalculationRequest & { refresh_incidents?: boolean; exclude_sources?: IncidentSource[] };
