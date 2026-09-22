import { compareChinaLocalDateTimes } from "./format";
import { reasonLabel } from "./presentation";
import type { CandidateResponse, RecommendationResponse, TrainResponse } from "./types";

/** Presentation thresholds. They never change backend ranking or status. */
export const NEAR_TIE_SCORE_DELTA = 0.05;
export const ROUTE_DURATION_MEANINGFUL_DELTA_SECONDS = 10 * 60;
export const TRAIN_COUNT_MEANINGFUL_DELTA = 2;

export interface NearTieComparison {
  alternative: CandidateResponse;
  message: string;
  facts: string[];
}

function routeDuration(candidate: CandidateResponse): number | null {
  const value = candidate.explanation?.route_duration_seconds ?? candidate.route?.duration_seconds;
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null;
}

function trainTime(candidate: CandidateResponse): TrainResponse | null {
  return candidate.earliest_recommended_train;
}

/**
 * Identify a close second-place GOOD candidate for a neutral UI note.
 * The candidates remain in backend order and their status/rank is untouched.
 */
export function getNearTieComparison(
  candidates: CandidateResponse[],
  recommendation: RecommendationResponse | null,
): NearTieComparison | null {
  if (!recommendation) return null;

  const recommended = candidates.find(
    (candidate) => candidate.hub.id === recommendation.candidate_hub_id,
  );
  const alternative = candidates.find((candidate) => candidate.rank === 2);
  if (
    !recommended ||
    recommended.rank !== 1 ||
    recommendation.status !== "RECOMMENDED" ||
    !alternative ||
    alternative.status !== "GOOD"
  ) {
    return null;
  }

  const scoreDelta = recommended.score - alternative.score;
  const thresholdEpsilon = 1e-9;
  if (
    !Number.isFinite(scoreDelta) ||
    scoreDelta < 0 ||
    scoreDelta > NEAR_TIE_SCORE_DELTA + thresholdEpsilon
  ) {
    return null;
  }

  const facts: string[] = [];
  const recommendedRoute = routeDuration(recommended);
  const alternativeRoute = routeDuration(alternative);
  if (
    recommendedRoute !== null &&
    alternativeRoute !== null &&
    Math.abs(recommendedRoute - alternativeRoute) >= ROUTE_DURATION_MEANINGFUL_DELTA_SECONDS
  ) {
    facts.push(
      recommendedRoute < alternativeRoute
        ? `${recommended.hub.name}接驳时间更短`
        : `${alternative.hub.name}接驳时间更短`,
    );
  }

  const feasibleDelta = recommended.train_summary.feasible - alternative.train_summary.feasible;
  if (Math.abs(feasibleDelta) >= TRAIN_COUNT_MEANINGFUL_DELTA) {
    facts.push(
      feasibleDelta > 0
        ? `${recommended.hub.name}可行车次更多`
        : `${alternative.hub.name}可行车次更多`,
    );
  }

  const recommendedTrainDelta =
    recommended.train_summary.recommended - alternative.train_summary.recommended;
  if (Math.abs(recommendedTrainDelta) >= TRAIN_COUNT_MEANINGFUL_DELTA) {
    facts.push(
      recommendedTrainDelta > 0
        ? `${recommended.hub.name}较稳妥的车次更多`
        : `${alternative.hub.name}较稳妥的车次更多`,
    );
  }

  const recommendedTrain = trainTime(recommended);
  const alternativeTrain = trainTime(alternative);
  const timeDelta = compareChinaLocalDateTimes(
    recommendedTrain?.departure_at,
    alternativeTrain?.departure_at,
  );
  if (timeDelta !== null && Math.abs(timeDelta) >= ROUTE_DURATION_MEANINGFUL_DELTA_SECONDS * 1000) {
    facts.push(
      timeDelta < 0
        ? `${recommended.hub.name}较早有较稳妥车次`
        : `${alternative.hub.name}较早有较稳妥车次`,
    );
  }

  return {
    alternative,
    message: "两个方案都比较可行，可以根据接驳方式和车次偏好选择。",
    facts,
  };
}

export function candidateRoleLabel(
  candidate: CandidateResponse,
  nearTieCandidateId: string | null,
): string {
  if (candidate.status === "RECOMMENDED") return "首选";
  if (candidate.status === "GOOD" && candidate.hub.id === nearTieCandidateId) {
    return "值得考虑的备选";
  }
  if (candidate.status === "GOOD") return "可行备选";
  if (candidate.status === "RISKY") return "时间偏紧";
  return "当前不可行";
}

export function reasonFragments(codes: string[]): string[] {
  return Array.from(new Set(codes.map((code) => reasonLabel(code))));
}
