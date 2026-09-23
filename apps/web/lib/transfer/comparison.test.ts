import { describe, expect, it } from "vitest";

import {
  candidateRoleLabel,
  getNearTieComparison,
  NEAR_TIE_SCORE_DELTA,
  reasonFragments,
} from "./comparison";
import type { CandidateResponse, RecommendationResponse } from "./types";

const train = {
  train_no: "C6315",
  train_type: "C",
  train_class: "C",
  service_date: "2026-09-18",
  origin_station_code: "A",
  origin_station_name: "成都东站",
  destination_station_code: "B",
  destination_station_name: "乐山站",
  departure_at: "2026-09-18T17:35:00+08:00",
  arrival_at: "2026-09-18T18:30:00+08:00",
  duration_seconds: 3_300,
  connection_status: "SAFE" as const,
  reason_codes: [],
};

function candidate(
  id: string,
  rank: number,
  status: CandidateResponse["status"],
  score: number,
  overrides: Partial<CandidateResponse> = {},
): CandidateResponse {
  return {
    hub: { id, name: id },
    rank,
    status,
    score,
    best_mode: "TRANSIT",
    route: {
      mode: "TRANSIT",
      duration_seconds: 5_000,
      distance_meters: 12_000,
      walking_distance_meters: 700,
      transfer_count: 1,
      segments: [],
      provider: "FIXTURE",
      fetched_at: "2026-09-18T14:20:00+08:00",
      confidence: "FIXTURE",
    },
    safe_transfer: null,
    route_evaluations: [],
    train_summary: { total: 5, feasible: 3, tight: 1, safe: 1, spacious: 1, recommended: 2 },
    earliest_feasible_train: train,
    earliest_recommended_train: train,
    reasons: [],
    partial_failures: [],
    data_status: "COMPLETE",
    explanation: null,
    ...overrides,
  };
}

const recommendation: RecommendationResponse = {
  candidate_hub_id: "east",
  candidate_hub_name: "east",
  status: "RECOMMENDED",
  score: 0.8,
};

describe("candidate comparison presentation helpers", () => {
  it("marks the exact near-tie threshold without changing candidate data", () => {
    const first = candidate("east", 1, "RECOMMENDED", 0.8);
    const second = candidate("south", 2, "GOOD", 0.8 - NEAR_TIE_SCORE_DELTA, {
      route: { ...first.route!, duration_seconds: 6_000 },
      train_summary: { total: 3, feasible: 1, tight: 1, safe: 0, spacious: 0, recommended: 0 },
    });
    const result = getNearTieComparison([first, second], recommendation);

    expect(result?.alternative.hub.id).toBe("south");
    expect(result?.message).toContain("两个方案都比较可行");
    expect(result?.facts).toContain("east接驳时间更短");
    expect(result?.facts).toContain("east可行车次更多");
    expect(result?.facts).toContain("east较稳妥的车次更多");
    expect(first.status).toBe("RECOMMENDED");
    expect(second.status).toBe("GOOD");
  });

  it("does not mark a score delta outside the presentation threshold as near-tie", () => {
    const first = candidate("east", 1, "RECOMMENDED", 0.8);
    const second = candidate("south", 2, "GOOD", 0.8 - NEAR_TIE_SCORE_DELTA - 0.001);

    expect(getNearTieComparison([first, second], recommendation)).toBeNull();
    expect(
      getNearTieComparison([first, { ...second, status: "RISKY" }], recommendation),
    ).toBeNull();
  });

  it("uses presentation-only candidate roles and deduplicated reason fragments", () => {
    expect(candidateRoleLabel(candidate("east", 1, "RECOMMENDED", 0.8), null)).toBe("首选");
    expect(candidateRoleLabel(candidate("south", 2, "GOOD", 0.7), "south")).toBe("值得考虑的备选");
    expect(candidateRoleLabel(candidate("west", 3, "GOOD", 0.6), null)).toBe("可行备选");
    expect(candidateRoleLabel(candidate("north", 4, "RISKY", 0.4), null)).toBe("时间偏紧");
    expect(candidateRoleLabel(candidate("station", 5, "INFEASIBLE", 0), null)).toBe("当前不可行");
    expect(reasonFragments(["MANY_FEASIBLE_TRAINS", "MANY_FEASIBLE_TRAINS"])).toEqual([
      "可选车次较多",
    ]);
  });
});
