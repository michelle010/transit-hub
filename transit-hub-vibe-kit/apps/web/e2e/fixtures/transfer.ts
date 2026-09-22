import type {
  AlternativeArrivalAirportResponse,
  BackupTrainRobustness,
  CandidateDataStatus,
  CandidateResponse,
  CandidateRouteResponse,
  FlexibleDateEvaluationResponse,
  RouteMode,
  TransferKind,
  TransferRequestResponse,
  TransferMetaResponse,
  TransferEvaluationResponse,
  TransferWarningCode,
  TrainResponse,
} from "../../lib/transfer/types";

const arrivalAt = "2026-09-18T14:20:00+08:00";
const evaluatedAt = "2026-09-18T06:30:00Z";
const routeFetchedAt = "2026-09-18T06:28:00Z";

const city = {
  id: "00000000-0000-0000-0000-000000000001",
  name: "成都",
};
const arrivalHub = {
  id: "10000000-0000-0000-0000-000000000001",
  name: "成都天府国际机场",
};
const destinationCity = {
  id: "00000000-0000-0000-0000-000000000002",
  name: "乐山",
};

function route(mode: RouteMode, durationSeconds: number, transferCount: number | null) {
  return {
    mode,
    duration_seconds: durationSeconds,
    distance_meters: mode === "DRIVING" ? 56_709 : 72_400,
    walking_distance_meters: mode === "TRANSIT" ? 591 : null,
    transfer_count: transferCount,
    segments: [
      ...(mode === "TRANSIT"
        ? [
            {
              mode,
              segment_type: "WALK" as const,
              label: "步行接驳",
              instruction: "步行前往地铁站",
              duration_seconds: 420,
              distance_meters: 591,
              line_name: null,
            },
            {
              mode,
              segment_type: "SUBWAY" as const,
              label: "地铁 18 号线",
              instruction: "乘坐地铁 18 号线前往成都东站",
              duration_seconds: Math.max(0, durationSeconds - 420),
              distance_meters: 71_809,
              line_name: "地铁 18 号线",
              vehicle_type: "地铁",
              departure_stop: "天府机场站",
              arrival_stop: "成都东站",
              stop_count: 8,
            },
          ]
        : [
            {
              mode,
              segment_type: "DRIVING" as const,
              label: "机场高速",
              instruction: "沿机场高速前往成都东站",
              duration_seconds: durationSeconds,
              distance_meters: 56_709,
              line_name: null,
              vehicle_type: "DRIVING",
            },
          ]),
    ],
    provider: "FIXTURE",
    fetched_at: routeFetchedAt,
    confidence: "FIXTURE",
  };
}

function safeTransfer(
  mode: RouteMode | null,
  routeDurationSeconds: number,
  theoretical: string,
  recommended: string,
  transferKind: TransferKind = "AIRPORT_TO_RAILWAY",
) {
  const railway = transferKind !== "AIRPORT_TO_RAILWAY";
  const sameStation = transferKind === "RAILWAY_SAME_STATION";
  const arrivalReleaseSeconds = railway ? 600 : 900;
  const baggageSeconds = railway ? 0 : 1_500;
  const originInternalSeconds = railway ? 0 : 900;
  const stationEntrySeconds = sameStation ? 900 : 1_800;
  const riskSeconds = railway ? 1_200 : 1_500;
  const nonRouteBufferSeconds =
    arrivalReleaseSeconds +
    baggageSeconds +
    originInternalSeconds +
    stationEntrySeconds +
    riskSeconds;
  return {
    rules_version: "stt-v1",
    arrival_at: arrivalAt,
    transfer_kind: transferKind,
    route_mode: mode,
    route_duration_seconds: routeDurationSeconds,
    arrival_release_seconds: arrivalReleaseSeconds,
    baggage_seconds: baggageSeconds,
    origin_internal_seconds: originInternalSeconds,
    station_entry_seconds: stationEntrySeconds,
    risk_seconds: riskSeconds,
    non_route_buffer_seconds: nonRouteBufferSeconds,
    total_transfer_seconds: nonRouteBufferSeconds + routeDurationSeconds,
    theoretical_ready_at: theoretical,
    recommended_departure_after: recommended,
    disclaimer: "fixture timetable and route data for deterministic tests",
  };
}

function train(
  trainNo: string,
  departureAt: string,
  arrivalAtValue: string,
  status: TrainResponse["connection_status"],
  reasonCodes: string[] = [],
): TrainResponse {
  return {
    train_no: trainNo,
    train_type: trainNo.startsWith("G") ? "G" : "C",
    train_class: "EMU",
    service_date: departureAt.slice(0, 10),
    origin_station_code: "ICW",
    origin_station_name: "成都东站",
    destination_station_code: "LKW",
    destination_station_name: "乐山站",
    departure_at: departureAt,
    arrival_at: arrivalAtValue,
    duration_seconds: 3_600,
    connection_status: status,
    reason_codes: reasonCodes,
  };
}

function backupRobustness(trains: TrainResponse[]): BackupTrainRobustness {
  const recommended = trains.filter(
    (item) => item.connection_status === "SAFE" || item.connection_status === "SPACIOUS",
  );
  const primary = recommended[0] ?? null;
  if (!primary) {
    return {
      status: "NO_PRIMARY_TRAIN",
      primary_train: null,
      backup_train: null,
      backup_available: false,
      backup_departure_gap_seconds: null,
    };
  }
  const backup = recommended.find(
    (item) => Date.parse(item.departure_at) > Date.parse(primary.departure_at),
  );
  return {
    status: backup ? "BACKUP_AVAILABLE" : "NO_BACKUP",
    primary_train: primary,
    backup_train: backup ?? null,
    backup_available: backup !== undefined,
    backup_departure_gap_seconds: backup
      ? Math.floor((Date.parse(backup.departure_at) - Date.parse(primary.departure_at)) / 1000)
      : null,
  };
}

function routeEvaluation(
  mode: RouteMode,
  routeValue: ReturnType<typeof route> | null,
  safeTransferValue: ReturnType<typeof safeTransfer> | null,
  trains: TrainResponse[],
  errors: CandidateRouteResponse["errors"] = [],
): CandidateRouteResponse {
  const counts = trains.reduce(
    (result, item) => {
      result.total += 1;
      if (item.connection_status === "TIGHT") result.tight += 1;
      if (item.connection_status === "SAFE") result.safe += 1;
      if (item.connection_status === "SPACIOUS") result.spacious += 1;
      if (item.connection_status && item.connection_status !== "INFEASIBLE") result.feasible += 1;
      if (item.connection_status === "SAFE" || item.connection_status === "SPACIOUS") {
        result.recommended += 1;
      }
      return result;
    },
    { total: 0, feasible: 0, tight: 0, safe: 0, spacious: 0, recommended: 0 },
  );
  return {
    mode,
    route: routeValue,
    safe_transfer: safeTransferValue,
    train_summary: counts,
    earliest_feasible_train: trains.find((item) => item.connection_status !== "INFEASIBLE") ?? null,
    earliest_recommended_train:
      trains.find(
        (item) => item.connection_status === "SAFE" || item.connection_status === "SPACIOUS",
      ) ?? null,
    train_connections: trains,
    errors,
    data_status: errors.length > 0 ? "PARTIAL" : "COMPLETE",
    train_robustness: backupRobustness(trains),
  };
}

function candidate(
  hubId: string,
  hubName: string,
  rank: number,
  status: CandidateResponse["status"],
  score: number,
  bestMode: RouteMode | null,
  routeEvaluations: CandidateRouteResponse[],
  reasons: string[],
  overrides: Partial<CandidateResponse> = {},
): CandidateResponse {
  const selected = bestMode
    ? (routeEvaluations.find((item) => item.mode === bestMode) ?? null)
    : (routeEvaluations[0] ?? null);
  const allTrains = selected?.train_connections ?? [];
  const trainSummary = selected?.train_summary ?? {
    total: 0,
    feasible: 0,
    tight: 0,
    safe: 0,
    spacious: 0,
    recommended: 0,
  };
  return {
    hub: { id: hubId, name: hubName },
    rank,
    status,
    score,
    best_mode: bestMode,
    route: selected?.route ?? null,
    safe_transfer: selected?.safe_transfer ?? null,
    route_evaluations: routeEvaluations,
    train_summary: trainSummary,
    earliest_feasible_train:
      selected?.earliest_feasible_train ??
      allTrains.find((item) => item.connection_status !== "INFEASIBLE") ??
      null,
    earliest_recommended_train: selected?.earliest_recommended_train ?? null,
    reasons,
    partial_failures: [],
    data_status: routeEvaluations.some((item) => item.data_status === "PARTIAL")
      ? "PARTIAL"
      : "COMPLETE",
    explanation: selected
      ? {
          route_duration_seconds: selected.route?.duration_seconds ?? null,
          feasible_train_count: trainSummary.feasible,
          recommended_train_count: trainSummary.recommended,
          safe_margin_seconds: selected.earliest_recommended_train ? 240 : null,
          transfer_count: selected.route?.transfer_count ?? null,
          walking_distance_meters: selected.route?.walking_distance_meters ?? null,
        }
      : null,
    train_robustness: selected?.train_robustness ?? null,
    ...overrides,
  };
}

function request(): TransferRequestResponse {
  return {
    transfer_city: city,
    arrival_hub: arrivalHub,
    destination_city: destinationCity,
    arrival_at: arrivalAt,
    baggage: "CHECKED" as const,
    allowed_modes: ["TRANSIT", "DRIVING"],
    rail_horizon_hours: 12,
    flexible_dates: { enabled: false, days_before: 1, days_after: 1 },
  };
}

function meta(
  dataCompleteness: CandidateDataStatus,
  warnings: TransferWarningCode[],
): TransferMetaResponse {
  return {
    evaluated_at: evaluatedAt,
    ranking_version: "ranking-v1",
    stt_version: "stt-v1",
    data_completeness: dataCompleteness,
    warnings,
    rail_search_window_start: arrivalAt,
    rail_search_window_end: "2026-09-19T02:20:00+08:00",
    provider_summary: { routing: "FIXTURE", rail: "FIXTURE_RAIL_GTFS" },
    railway_source: {
      provider: "CHINA_RAILWAY_GTFS",
      source_name: "fixture:rail-timetable",
      source_version: "fixture-v1",
      source_updated_at: "2026-09-18T00:00:00+08:00",
      service_date_start: "2026-09-01",
      service_date_end: "2026-10-31",
      freshness_status: "FRESH" as const,
      age_seconds: 0,
    },
  };
}

function completeCandidates(): CandidateResponse[] {
  const eastTrains = [
    train("G1", "2026-09-18T17:00:00+08:00", "2026-09-18T18:00:00+08:00", "INFEASIBLE", [
      "DEPARTS_BEFORE_THEORETICAL",
    ]),
    train("G2", "2026-09-18T17:15:00+08:00", "2026-09-18T18:15:00+08:00", "TIGHT", [
      "DEPARTS_BEFORE_RECOMMENDED",
    ]),
    train("G4", "2026-09-18T17:26:00+08:00", "2026-09-18T18:26:00+08:00", "TIGHT", [
      "DEPARTS_BEFORE_RECOMMENDED",
    ]),
    train("C5771/C5774", "2026-09-18T17:35:00+08:00", "2026-09-18T18:35:00+08:00", "SAFE", [
      "MEETS_RECOMMENDED_BUFFER",
    ]),
    train("G3", "2026-09-18T18:40:00+08:00", "2026-09-18T19:40:00+08:00", "SPACIOUS", [
      "HAS_SPACIOUS_MARGIN",
    ]),
    train("D972/D973B", "2026-09-19T09:10:00+08:00", "2026-09-19T10:10:00+08:00", "SAFE", [
      "MEETS_RECOMMENDED_BUFFER",
    ]),
  ];
  const southTrains = [
    train("C1", "2026-09-18T17:18:00+08:00", "2026-09-18T18:18:00+08:00", "SAFE", [
      "MEETS_RECOMMENDED_BUFFER",
    ]),
    train("C2", "2026-09-18T18:05:00+08:00", "2026-09-18T19:05:00+08:00", "SPACIOUS", [
      "HAS_SPACIOUS_MARGIN",
    ]),
  ];
  return [
    candidate(
      "10000000-0000-0000-0000-000000000002",
      "成都东站",
      1,
      "RECOMMENDED",
      0.84,
      "TRANSIT",
      [
        routeEvaluation(
          "TRANSIT",
          route("TRANSIT", 4_860, 1),
          safeTransfer("TRANSIT", 4_860, "2026-09-18T17:06:00+08:00", "2026-09-18T17:31:00+08:00"),
          eastTrains,
        ),
        routeEvaluation(
          "DRIVING",
          route("DRIVING", 2_566, null),
          safeTransfer("DRIVING", 2_566, "2026-09-18T16:27:46+08:00", "2026-09-18T16:52:46+08:00"),
          eastTrains,
        ),
      ],
      ["MANY_FEASIBLE_TRAINS", "GOOD_SAFE_MARGIN", "LOW_TRANSFER_COMPLEXITY"],
    ),
    candidate(
      "10000000-0000-0000-0000-000000000003",
      "成都南站",
      2,
      "GOOD",
      0.82,
      "TRANSIT",
      [
        routeEvaluation(
          "TRANSIT",
          route("TRANSIT", 3_600, 0),
          safeTransfer("TRANSIT", 3_600, "2026-09-18T16:45:00+08:00", "2026-09-18T17:10:00+08:00"),
          southTrains,
        ),
        routeEvaluation(
          "DRIVING",
          route("DRIVING", 2_400, null),
          safeTransfer("DRIVING", 2_400, "2026-09-18T16:25:00+08:00", "2026-09-18T16:50:00+08:00"),
          southTrains,
        ),
      ],
      ["SHORT_TRANSFER", "GOOD_SAFE_MARGIN"],
    ),
    candidate(
      "10000000-0000-0000-0000-000000000004",
      "成都西站",
      3,
      "INFEASIBLE",
      0.18,
      "TRANSIT",
      [
        routeEvaluation(
          "TRANSIT",
          route("TRANSIT", 7_200, 2),
          safeTransfer("TRANSIT", 7_200, "2026-09-18T18:00:00+08:00", "2026-09-18T18:25:00+08:00"),
          [],
        ),
        routeEvaluation(
          "DRIVING",
          route("DRIVING", 6_600, null),
          safeTransfer("DRIVING", 6_600, "2026-09-18T17:50:00+08:00", "2026-09-18T18:15:00+08:00"),
          [],
        ),
      ],
      ["NO_RAIL_SERVICE", "LONG_TRANSFER"],
    ),
  ];
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export type TransferScenario =
  | "complete"
  | "partial"
  | "risky"
  | "infeasible"
  | "nearby"
  | "airport_alternatives"
  | "flexible_dates"
  | "flexible_partial"
  | "flexible_unavailable"
  | "stale_source"
  | "unknown_source"
  | "railway_same_station"
  | "railway_cross_station"
  | "transit_unavailable"
  | "transit_only_unavailable"
  | "transit_provider_failure";

function railwayTransferFixture(
  kind: "RAILWAY_SAME_STATION" | "RAILWAY_CROSS_STATION",
): TransferEvaluationResponse {
  const result = transferFixture("complete");
  const east = result.candidates[0];
  const transit = east.route_evaluations.find((item) => item.mode === "TRANSIT");
  const driving = east.route_evaluations.find((item) => item.mode === "DRIVING");
  result.request.arrival_hub = {
    id: kind === "RAILWAY_SAME_STATION" ? east.hub.id : "10000000-0000-0000-0000-000000000005",
    name: kind === "RAILWAY_SAME_STATION" ? east.hub.name : "成都站",
  };
  const sameSafe = safeTransfer(
    null,
    0,
    "2026-09-18T14:45:00+08:00",
    "2026-09-18T15:05:00+08:00",
    kind,
  );
  const crossSafe = safeTransfer(
    "TRANSIT",
    3_600,
    "2026-09-18T16:00:00+08:00",
    "2026-09-18T16:20:00+08:00",
    kind,
  );
  if (transit) {
    transit.route = kind === "RAILWAY_SAME_STATION" ? null : transit.route;
    transit.safe_transfer = kind === "RAILWAY_SAME_STATION" ? sameSafe : crossSafe;
  }
  if (driving) {
    driving.route = kind === "RAILWAY_SAME_STATION" ? null : driving.route;
    driving.safe_transfer = kind === "RAILWAY_SAME_STATION" ? sameSafe : crossSafe;
  }
  east.route = kind === "RAILWAY_SAME_STATION" ? null : east.route;
  east.safe_transfer = kind === "RAILWAY_SAME_STATION" ? sameSafe : crossSafe;
  if (east.explanation) {
    east.explanation = {
      ...east.explanation,
      route_duration_seconds: kind === "RAILWAY_SAME_STATION" ? null : 3_600,
    };
  }
  result.meta.provider_summary = {
    ...result.meta.provider_summary,
    routing: "FIXTURE",
    rail: "CHINA_RAILWAY_GTFS",
  };
  return result;
}

function nearbyTransferFixture(): TransferEvaluationResponse {
  const result = transferFixture("complete");
  const east = result.candidates[0];
  const transit = east.route_evaluations.find((item) => item.mode === "TRANSIT");
  const nearbyTrain = transit?.train_connections.find((item) => item.train_no === "C5771/C5774");
  const nearbyDestination = {
    id: "20000000-0000-0000-0000-000000000001",
    name: "峨眉山站",
    is_nearby_alternative: true,
    distance_from_requested_destination_meters: 28_400,
  };
  if (nearbyTrain) {
    nearbyTrain.destination_hub = nearbyDestination;
  }
  const nearbyBackup = transit?.train_connections.find((item) => item.train_no === "G3");
  if (nearbyBackup) {
    nearbyBackup.destination_hub = nearbyDestination;
  }
  return result;
}

function airportAlternativeTransferFixture(): TransferEvaluationResponse {
  const result = transferFixture("complete");
  const referenceCandidate = clone(result.candidates[1]);
  referenceCandidate.rank = 1;
  referenceCandidate.status = "RECOMMENDED";
  const alternative: AlternativeArrivalAirportResponse = {
    arrival_hub: {
      id: "10000000-0000-0000-0000-000000000002",
      name: "成都双流国际机场",
    },
    distance_from_requested_arrival_meters: 56_000,
    recommendation: {
      candidate_hub_id: referenceCandidate.hub.id,
      candidate_hub_name: referenceCandidate.hub.name,
      status: "RECOMMENDED",
      score: referenceCandidate.score,
    },
    candidates: [referenceCandidate],
    candidate_count: 1,
    good_candidate_count: 1,
    risky_candidate_count: 0,
    infeasible_candidate_count: 0,
    data_completeness: "COMPLETE",
    warnings: [],
    meta: meta("COMPLETE", []),
    failure_code: null,
  };
  return { ...result, alternative_arrival_airports: [alternative] };
}

function flexibleDateEntry(
  date: string,
  isPrimary: boolean,
  candidate: CandidateResponse,
  overrides: Partial<FlexibleDateEvaluationResponse> = {},
): FlexibleDateEvaluationResponse {
  const earliest = candidate.earliest_recommended_train;
  return {
    date,
    arrival_at: `${date}T14:20:00+08:00`,
    is_primary: isPrimary,
    status: "AVAILABLE",
    convenience: {
      feasible_train_count: isPrimary ? 5 : 3,
      recommended_train_count: isPrimary ? 3 : 2,
      earliest_recommended_departure_at: earliest?.departure_at ?? null,
      best_connection_margin_minutes: isPrimary ? 42 : 18,
      service_span_minutes: isPrimary ? 420 : 180,
      service_distribution_bucket_count: isPrimary ? 3 : 2,
      service_distribution_bucket_total: 4,
      convenience_score: isPrimary ? 78 : 54,
    },
    recommendation: {
      candidate_hub_id: candidate.hub.id,
      candidate_hub_name: candidate.hub.name,
      status: "RECOMMENDED",
      score: candidate.score,
    },
    earliest_recommended_train: earliest,
    candidate_count: 3,
    data_completeness: "COMPLETE",
    warnings: [],
    failure_code: null,
    ...overrides,
  };
}

function flexibleDateTransferFixture(
  scenario: "flexible_dates" | "flexible_partial" | "flexible_unavailable",
): TransferEvaluationResponse {
  const result = transferFixture("complete");
  const candidate = result.candidates[0];
  const dates: FlexibleDateEvaluationResponse[] = [
    flexibleDateEntry("2026-09-17", false, candidate, {
      convenience: {
        feasible_train_count: 2,
        recommended_train_count: 1,
        earliest_recommended_departure_at: "2026-09-17T18:20:00+08:00",
        best_connection_margin_minutes: 18,
        service_span_minutes: 160,
        service_distribution_bucket_count: 2,
        service_distribution_bucket_total: 4,
        convenience_score: 54,
      },
      earliest_recommended_train: train(
        "G-SEP17",
        "2026-09-17T18:20:00+08:00",
        "2026-09-17T19:20:00+08:00",
        "SAFE",
      ),
    }),
    flexibleDateEntry("2026-09-18", true, candidate),
    flexibleDateEntry("2026-09-19", false, candidate, {
      convenience: {
        feasible_train_count: 4,
        recommended_train_count: 2,
        earliest_recommended_departure_at: "2026-09-19T17:50:00+08:00",
        best_connection_margin_minutes: 30,
        service_span_minutes: 280,
        service_distribution_bucket_count: 2,
        service_distribution_bucket_total: 4,
        convenience_score: 63,
      },
      earliest_recommended_train: train(
        "G-SEP19",
        "2026-09-19T17:50:00+08:00",
        "2026-09-19T18:50:00+08:00",
        "SAFE",
      ),
    }),
  ];
  if (scenario === "flexible_partial") {
    dates[0] = flexibleDateEntry("2026-09-17", false, candidate, {
      status: "PARTIAL",
      data_completeness: "PARTIAL",
      warnings: ["PARTIAL_PROVIDER_DATA"],
      convenience: {
        feasible_train_count: 1,
        recommended_train_count: 1,
        earliest_recommended_departure_at: "2026-09-17T18:20:00+08:00",
        best_connection_margin_minutes: 10,
        service_span_minutes: 0,
        service_distribution_bucket_count: 1,
        service_distribution_bucket_total: 4,
        convenience_score: 22,
      },
    });
  }
  if (scenario === "flexible_unavailable") {
    dates[2] = flexibleDateEntry("2026-09-19", false, candidate, {
      status: "UNAVAILABLE",
      convenience: null,
      recommendation: null,
      earliest_recommended_train: null,
      candidate_count: 0,
      data_completeness: "UNAVAILABLE",
      warnings: ["PARTIAL_PROVIDER_DATA"],
      failure_code: "RAIL_DATA_OUT_OF_RANGE",
    });
  }
  return {
    ...result,
    request: {
      ...result.request,
      flexible_dates: { enabled: true, days_before: 1, days_after: 1 },
    },
    flexible_date_comparison: {
      primary_date: "2026-09-18",
      days_before: 1,
      days_after: 1,
      dates,
    },
  };
}

export function transferFixture(scenario: TransferScenario): TransferEvaluationResponse {
  if (scenario === "railway_same_station") {
    return railwayTransferFixture("RAILWAY_SAME_STATION");
  }
  if (scenario === "railway_cross_station") {
    return railwayTransferFixture("RAILWAY_CROSS_STATION");
  }
  if (scenario === "stale_source" || scenario === "unknown_source") {
    const result = transferFixture("complete");
    result.meta.railway_source = {
      ...result.meta.railway_source!,
      source_updated_at: scenario === "stale_source" ? "2026-01-01T00:00:00+08:00" : null,
      freshness_status: scenario === "stale_source" ? "STALE" : "UNKNOWN",
      age_seconds: scenario === "stale_source" ? 22_000_000 : null,
    };
    return result;
  }
  if (scenario === "nearby") return nearbyTransferFixture();
  if (scenario === "airport_alternatives") return airportAlternativeTransferFixture();
  if (
    scenario === "flexible_dates" ||
    scenario === "flexible_partial" ||
    scenario === "flexible_unavailable"
  ) {
    return flexibleDateTransferFixture(scenario);
  }
  if (scenario === "transit_unavailable") {
    return routeAvailabilityFixture("UNAVAILABLE", false);
  }
  if (scenario === "transit_only_unavailable") {
    return routeAvailabilityFixture("UNAVAILABLE", true);
  }
  if (scenario === "transit_provider_failure") {
    return routeAvailabilityFixture("PROVIDER_FAILURE", false);
  }
  const candidates = completeCandidates();
  if (scenario === "complete") {
    return {
      request: request(),
      recommendation: {
        candidate_hub_id: candidates[0].hub.id,
        candidate_hub_name: candidates[0].hub.name,
        status: "RECOMMENDED",
        score: candidates[0].score,
      },
      candidates,
      candidate_count: 3,
      good_candidate_count: 2,
      risky_candidate_count: 0,
      infeasible_candidate_count: 1,
      data_completeness: "COMPLETE",
      warnings: [],
      meta: meta("COMPLETE", []),
    };
  }

  if (scenario === "partial") {
    const partial = clone(candidates);
    const east = partial[0];
    const driving = east.route_evaluations.find((item) => item.mode === "DRIVING");
    if (driving) {
      driving.route = null;
      driving.safe_transfer = null;
      driving.errors = [{ code: "ROUTE_MODE_UNAVAILABLE", route_mode: "DRIVING" }];
      driving.data_status = "PARTIAL";
    }
    east.data_status = "PARTIAL";
    east.partial_failures = [{ code: "ROUTE_MODE_UNAVAILABLE", route_mode: "DRIVING" }];
    return {
      ...transferFixture("complete"),
      candidates: partial,
      data_completeness: "PARTIAL",
      warnings: ["PARTIAL_PROVIDER_DATA"],
      meta: meta("PARTIAL", ["PARTIAL_PROVIDER_DATA"]),
    };
  }

  if (scenario === "risky") {
    const riskyCandidate = clone(candidates[0]);
    const tightTrain = train(
      "K88",
      "2026-09-18T17:15:00+08:00",
      "2026-09-18T18:15:00+08:00",
      "TIGHT",
      ["DEPARTS_BEFORE_RECOMMENDED"],
    );
    riskyCandidate.status = "RISKY";
    riskyCandidate.rank = 1;
    riskyCandidate.score = 0.54;
    riskyCandidate.reasons = ["ONLY_TIGHT_CONNECTIONS", "SHORT_TRANSFER"];
    riskyCandidate.route_evaluations = [
      routeEvaluation(
        "TRANSIT",
        route("TRANSIT", 2_700, 1),
        safeTransfer("TRANSIT", 2_700, "2026-09-18T16:45:00+08:00", "2026-09-18T17:10:00+08:00"),
        [tightTrain],
      ),
    ];
    riskyCandidate.best_mode = "TRANSIT";
    riskyCandidate.route = riskyCandidate.route_evaluations[0].route;
    riskyCandidate.safe_transfer = riskyCandidate.route_evaluations[0].safe_transfer;
    riskyCandidate.train_summary = riskyCandidate.route_evaluations[0].train_summary;
    riskyCandidate.train_robustness = riskyCandidate.route_evaluations[0].train_robustness;
    riskyCandidate.earliest_feasible_train = tightTrain;
    riskyCandidate.earliest_recommended_train = null;
    return {
      ...transferFixture("complete"),
      recommendation: null,
      candidates: [riskyCandidate],
      candidate_count: 1,
      good_candidate_count: 0,
      risky_candidate_count: 1,
      infeasible_candidate_count: 0,
      warnings: ["NO_SAFE_RECOMMENDATION"],
      meta: meta("COMPLETE", ["NO_SAFE_RECOMMENDATION"]),
    };
  }

  const infeasibleCandidate = clone(candidates[2]);
  return {
    ...transferFixture("complete"),
    recommendation: null,
    candidates: [infeasibleCandidate],
    candidate_count: 1,
    good_candidate_count: 0,
    risky_candidate_count: 0,
    infeasible_candidate_count: 1,
    warnings: ["NO_FEASIBLE_CONNECTION"],
    meta: meta("COMPLETE", ["NO_FEASIBLE_CONNECTION"]),
  };
}

function routeAvailabilityFixture(
  availability: "UNAVAILABLE" | "PROVIDER_FAILURE",
  transitOnly: boolean,
): TransferEvaluationResponse {
  const result = transferFixture("complete");
  const candidate = clone(result.candidates[0]);
  const transit = candidate.route_evaluations.find((item) => item.mode === "TRANSIT");
  if (!transit) throw new Error("fixture candidate is missing transit route");
  const failure = {
    code: "ROUTE_MODE_UNAVAILABLE",
    route_mode: "TRANSIT" as const,
    availability: availability as "UNAVAILABLE" | "PROVIDER_FAILURE",
    reason:
      availability === "UNAVAILABLE" ? ("NO_ROUTE" as const) : ("PROVIDER_UNAVAILABLE" as const),
  };
  transit.route = null;
  transit.safe_transfer = null;
  transit.errors = [failure];
  transit.route_availability = availability;
  transit.route_failure_reason = failure.reason;
  transit.data_status = "PARTIAL";
  candidate.partial_failures = [failure];
  candidate.data_status = "PARTIAL";
  if (transitOnly) {
    candidate.route_evaluations = [transit];
    candidate.best_mode = null;
    candidate.route = null;
    candidate.safe_transfer = null;
    candidate.train_summary = {
      total: 0,
      feasible: 0,
      tight: 0,
      safe: 0,
      spacious: 0,
      recommended: 0,
    };
    candidate.earliest_feasible_train = null;
    candidate.earliest_recommended_train = null;
    candidate.train_robustness = null;
    candidate.explanation = null;
    candidate.status = "INFEASIBLE";
    candidate.score = 0;
    candidate.reasons = ["ROUTE_MODE_UNAVAILABLE"];
  }
  return {
    ...result,
    request: {
      ...result.request,
      allowed_modes: transitOnly ? ["TRANSIT"] : ["TRANSIT", "DRIVING"],
    },
    recommendation: transitOnly ? null : result.recommendation,
    candidates: [candidate],
    candidate_count: 1,
    good_candidate_count: transitOnly ? 0 : 1,
    risky_candidate_count: 0,
    infeasible_candidate_count: transitOnly ? 1 : 0,
    data_completeness: "PARTIAL",
    warnings: ["PARTIAL_PROVIDER_DATA"],
    meta: meta("PARTIAL", ["PARTIAL_PROVIDER_DATA"]),
  };
}

export const completeTransferFixture = transferFixture("complete");
