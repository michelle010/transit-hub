export type BaggageStatus = "NONE" | "CHECKED" | "UNKNOWN";

export type RouteMode = "TRANSIT" | "DRIVING";

export type RouteAvailability =
  "AVAILABLE" | "UNAVAILABLE" | "PROVIDER_FAILURE" | "QUOTA_OR_BUDGET_FAILURE";

export type RouteFailureReason =
  | "NO_ROUTE"
  | "PROVIDER_UNAVAILABLE"
  | "TIMEOUT"
  | "QUOTA_EXCEEDED"
  | "OPERATION_BUDGET_EXHAUSTED"
  | "AUTHENTICATION"
  | "INVALID_REQUEST"
  | "RESPONSE_ERROR"
  | "UNKNOWN";

export type TransferKind = "AIRPORT_TO_RAILWAY" | "RAILWAY_SAME_STATION" | "RAILWAY_CROSS_STATION";

export type RouteSegmentType =
  "TRANSIT" | "WALK" | "BUS" | "SUBWAY" | "RAILWAY" | "TAXI" | "DRIVING";

export type CandidateStatus = "RECOMMENDED" | "GOOD" | "RISKY" | "INFEASIBLE";

export type CandidateDataStatus = "COMPLETE" | "PARTIAL" | "UNAVAILABLE";

export type FlexibleDateStatus = "AVAILABLE" | "PARTIAL" | "UNAVAILABLE";

export type RailwayFreshnessStatus = "FRESH" | "STALE" | "UNKNOWN";

export type ConnectionStatus = "INFEASIBLE" | "TIGHT" | "SAFE" | "SPACIOUS";

export type BackupTrainStatus = "BACKUP_AVAILABLE" | "NO_BACKUP" | "NO_PRIMARY_TRAIN";

export type TransferWarningCode =
  "NO_SAFE_RECOMMENDATION" | "NO_FEASIBLE_CONNECTION" | "PARTIAL_PROVIDER_DATA";

export interface TransferEvaluateRequest {
  transfer_city: string;
  arrival_hub: string;
  destination_city: string;
  arrival_at: string;
  baggage: BaggageStatus;
  allowed_modes: RouteMode[];
  rail_horizon_hours: number;
  include_alternative_hubs?: boolean;
  flexible_dates?: FlexibleDateOptions;
}

export interface FlexibleDateOptions {
  enabled: boolean;
  days_before: number;
  days_after: number;
}

export interface EntityResponse {
  id: string;
  name: string;
}

export interface TransferRequestResponse {
  transfer_city: EntityResponse;
  arrival_hub: EntityResponse;
  destination_city: EntityResponse;
  arrival_at: string;
  baggage: BaggageStatus;
  allowed_modes: RouteMode[];
  rail_horizon_hours: number;
  include_alternative_hubs?: boolean;
  flexible_dates?: FlexibleDateOptions | null;
}

export interface RouteSegmentResponse {
  mode: RouteMode;
  segment_type: RouteSegmentType;
  label: string;
  instruction?: string | null;
  duration_seconds?: number | null;
  distance_meters?: number | null;
  line_name?: string | null;
  vehicle_type?: string | null;
  departure_stop?: string | null;
  arrival_stop?: string | null;
  stop_count?: number | null;
}

export interface RouteResponse {
  mode: RouteMode;
  duration_seconds: number;
  distance_meters: number | null;
  walking_distance_meters: number | null;
  transfer_count: number | null;
  segments: RouteSegmentResponse[];
  provider: string;
  fetched_at: string;
  confidence: string;
}

export interface SafeTransferResponse {
  rules_version: string;
  arrival_at: string;
  transfer_kind?: TransferKind | null;
  route_mode: RouteMode | null;
  route_duration_seconds: number;
  arrival_release_seconds: number;
  baggage_seconds: number;
  origin_internal_seconds: number;
  station_entry_seconds: number;
  risk_seconds: number;
  non_route_buffer_seconds: number;
  total_transfer_seconds: number;
  theoretical_ready_at: string;
  recommended_departure_after: string;
  disclaimer: string;
}

export interface TrainResponse {
  train_no: string;
  train_type: string | null;
  train_class: string | null;
  service_date: string;
  origin_station_code: string;
  origin_station_name: string;
  destination_station_code: string;
  destination_station_name: string;
  departure_at: string;
  arrival_at: string;
  duration_seconds: number;
  source_updated_at?: string | null;
  connection_status: ConnectionStatus | null;
  reason_codes: string[];
  destination_hub?: DestinationHubResponse | null;
}

export interface BackupTrainRobustness {
  status: BackupTrainStatus;
  primary_train: TrainResponse | null;
  backup_train: TrainResponse | null;
  backup_available: boolean;
  backup_departure_gap_seconds: number | null;
}

export interface DestinationHubResponse {
  id: string;
  name: string;
  is_nearby_alternative: boolean;
  distance_from_requested_destination_meters: number;
}

export interface TrainSummaryResponse {
  total: number;
  feasible: number;
  tight: number;
  safe: number;
  spacious: number;
  recommended: number;
}

export interface PartialFailureResponse {
  code: string;
  route_mode: RouteMode | null;
  availability?: RouteAvailability | null;
  reason?: RouteFailureReason | null;
}

export interface CandidateRouteResponse {
  mode: RouteMode;
  route: RouteResponse | null;
  safe_transfer: SafeTransferResponse | null;
  train_summary: TrainSummaryResponse;
  earliest_feasible_train: TrainResponse | null;
  earliest_recommended_train: TrainResponse | null;
  train_connections: TrainResponse[];
  errors: PartialFailureResponse[];
  data_status: CandidateDataStatus;
  route_availability?: RouteAvailability | null;
  route_failure_reason?: RouteFailureReason | null;
  train_robustness?: BackupTrainRobustness | null;
}

export interface CandidateExplanation {
  route_duration_seconds: number | null;
  feasible_train_count: number;
  recommended_train_count: number;
  safe_margin_seconds: number | null;
  transfer_count: number | null;
  walking_distance_meters: number | null;
}

export interface CandidateResponse {
  hub: EntityResponse;
  rank: number | null;
  status: CandidateStatus;
  score: number;
  best_mode: RouteMode | null;
  route: RouteResponse | null;
  safe_transfer: SafeTransferResponse | null;
  route_evaluations: CandidateRouteResponse[];
  train_summary: TrainSummaryResponse;
  earliest_feasible_train: TrainResponse | null;
  earliest_recommended_train: TrainResponse | null;
  reasons: string[];
  partial_failures: PartialFailureResponse[];
  data_status: CandidateDataStatus;
  explanation: CandidateExplanation | null;
  train_robustness?: BackupTrainRobustness | null;
}

export interface RecommendationResponse {
  candidate_hub_id: string;
  candidate_hub_name: string;
  status: CandidateStatus;
  score: number;
}

export interface TransferMetaResponse {
  evaluated_at: string;
  ranking_version: string;
  stt_version: string;
  data_completeness: CandidateDataStatus;
  warnings: TransferWarningCode[];
  rail_search_window_start: string;
  rail_search_window_end: string;
  provider_summary: Record<string, unknown>;
  railway_source?: RailwaySourceMetadata | null;
}

export interface RailwaySourceMetadata {
  provider: string;
  source_name: string;
  source_version?: string | null;
  source_updated_at?: string | null;
  service_date_start?: string | null;
  service_date_end?: string | null;
  freshness_status: RailwayFreshnessStatus;
  age_seconds?: number | null;
}

export interface AlternativeArrivalAirportResponse {
  arrival_hub: EntityResponse;
  distance_from_requested_arrival_meters: number;
  recommendation: RecommendationResponse | null;
  candidates: CandidateResponse[];
  candidate_count: number;
  good_candidate_count: number;
  risky_candidate_count: number;
  infeasible_candidate_count: number;
  data_completeness: CandidateDataStatus;
  warnings: TransferWarningCode[];
  meta: TransferMetaResponse;
  failure_code?: string | null;
}

export interface DateConvenienceMetricsResponse {
  feasible_train_count: number;
  recommended_train_count: number;
  earliest_recommended_departure_at: string | null;
  best_connection_margin_minutes: number | null;
  service_span_minutes: number;
  service_distribution_bucket_count: number;
  service_distribution_bucket_total: number;
  convenience_score: number;
}

export interface FlexibleDateEvaluationResponse {
  date: string;
  arrival_at: string;
  is_primary: boolean;
  status: FlexibleDateStatus;
  convenience: DateConvenienceMetricsResponse | null;
  recommendation: RecommendationResponse | null;
  earliest_recommended_train: TrainResponse | null;
  candidate_count: number;
  data_completeness: CandidateDataStatus;
  warnings: TransferWarningCode[];
  failure_code?: string | null;
}

export interface FlexibleDateComparisonResponse {
  primary_date: string;
  days_before: number;
  days_after: number;
  dates: FlexibleDateEvaluationResponse[];
}

export interface TransferEvaluationResponse {
  request: TransferRequestResponse;
  recommendation: RecommendationResponse | null;
  candidates: CandidateResponse[];
  candidate_count: number;
  good_candidate_count: number;
  risky_candidate_count: number;
  infeasible_candidate_count: number;
  data_completeness: CandidateDataStatus;
  warnings: TransferWarningCode[];
  meta: TransferMetaResponse;
  alternative_arrival_airports?: AlternativeArrivalAirportResponse[];
  flexible_date_comparison?: FlexibleDateComparisonResponse | null;
}
