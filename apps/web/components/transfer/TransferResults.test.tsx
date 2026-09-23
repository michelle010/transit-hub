import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import TransferResults from "./TransferResults";
import type { CandidateResponse, TransferEvaluationResponse } from "@/lib/transfer/types";

const arrivalAt = "2026-09-18T14:20:00+08:00";

function candidate(
  name: string,
  status: CandidateResponse["status"],
  rank: number,
  overrides: Partial<CandidateResponse> = {},
): CandidateResponse {
  return {
    hub: { id: `${rank}`, name },
    rank,
    status,
    score: 0.7,
    best_mode: "TRANSIT",
    route: {
      mode: "TRANSIT",
      duration_seconds: 5_253,
      distance_meters: 10_000,
      walking_distance_meters: 500,
      transfer_count: 1,
      segments: [],
      provider: "FIXTURE",
      fetched_at: arrivalAt,
      confidence: "FIXTURE",
    },
    safe_transfer: {
      rules_version: "stt-v1",
      arrival_at: arrivalAt,
      route_mode: "TRANSIT",
      route_duration_seconds: 5_253,
      arrival_release_seconds: 900,
      baggage_seconds: 1_500,
      origin_internal_seconds: 900,
      station_entry_seconds: 1_800,
      risk_seconds: 1_500,
      non_route_buffer_seconds: 6_600,
      total_transfer_seconds: 11_853,
      theoretical_ready_at: "2026-09-18T17:01:00+08:00",
      recommended_departure_after: "2026-09-18T17:26:00+08:00",
      disclaimer: "planning guidance",
    },
    route_evaluations: [],
    train_summary: { total: 3, feasible: 2, tight: 1, safe: 1, spacious: 1, recommended: 2 },
    earliest_feasible_train: null,
    earliest_recommended_train: {
      train_no: "C6315",
      train_type: "C",
      train_class: "C",
      service_date: "2026-09-18",
      origin_station_code: "A",
      origin_station_name: name,
      destination_station_code: "B",
      destination_station_name: "乐山站",
      departure_at: "2026-09-18T17:35:00+08:00",
      arrival_at: "2026-09-18T18:30:00+08:00",
      duration_seconds: 3_300,
      connection_status: "SAFE",
      reason_codes: [],
    },
    reasons: ["MANY_FEASIBLE_TRAINS"],
    partial_failures: [],
    data_status: "COMPLETE",
    explanation: null,
    ...overrides,
  };
}

function response(overrides: Partial<TransferEvaluationResponse> = {}): TransferEvaluationResponse {
  return {
    request: {
      transfer_city: { id: "city", name: "成都" },
      arrival_hub: { id: "airport", name: "成都天府国际机场" },
      destination_city: { id: "destination", name: "乐山" },
      arrival_at: arrivalAt,
      baggage: "CHECKED",
      allowed_modes: ["TRANSIT", "DRIVING"],
      rail_horizon_hours: 12,
    },
    recommendation: {
      candidate_hub_id: "1",
      candidate_hub_name: "成都东站",
      status: "RECOMMENDED",
      score: 0.75,
    },
    candidates: [candidate("成都东站", "RECOMMENDED", 1), candidate("成都南站", "GOOD", 2)],
    candidate_count: 2,
    good_candidate_count: 1,
    risky_candidate_count: 0,
    infeasible_candidate_count: 0,
    data_completeness: "COMPLETE",
    warnings: [],
    meta: {
      evaluated_at: arrivalAt,
      ranking_version: "ranking-v1",
      stt_version: "stt-v1",
      data_completeness: "COMPLETE",
      warnings: [],
      rail_search_window_start: arrivalAt,
      rail_search_window_end: "2026-09-19T02:20:00+08:00",
      provider_summary: {},
    },
    ...overrides,
  };
}

describe("TransferResults", () => {
  it("renders the recommendation and preserves backend candidate order", () => {
    const html = renderToStaticMarkup(<TransferResults data={response()} />);

    expect(html).toContain("综合比较后的第一名");
    expect(html).toContain("成都东站");
    expect(html.indexOf('aria-labelledby="candidate-1"')).toBeLessThan(
      html.indexOf('aria-labelledby="candidate-2"'),
    );
    expect(html).toContain("首选");
    expect(html).toContain("值得考虑的备选");
    expect(html).toContain("两个方案都比较可行");
    expect(html).toContain("C6315 · 17:35");
  });

  it("renders fresh railway source provenance without changing the recommendation", () => {
    const html = renderToStaticMarkup(
      <TransferResults
        data={response({
          meta: {
            ...response().meta,
            railway_source: {
              provider: "CHINA_RAILWAY_GTFS",
              source_name: "fixture:rail-timetable",
              source_version: "fixture-v1",
              source_updated_at: "2026-09-18T00:00:00+08:00",
              service_date_start: "2026-09-01",
              service_date_end: "2026-10-31",
              freshness_status: "FRESH",
              age_seconds: 0,
            },
          },
        })}
      />,
    );

    expect(html).toContain("铁路时刻数据来源");
    expect(html).toContain("CHINA_RAILWAY_GTFS");
    expect(html).toContain("来源更新时间：2026-09-18 00:00");
    expect(html).toContain("服务日期覆盖：2026-09-01 至 2026-10-31");
    expect(html).toContain("综合比较后的第一名");
  });

  it("shows a visible warning for stale railway data", () => {
    const data = response({
      meta: {
        ...response().meta,
        railway_source: {
          provider: "CHINA_RAILWAY_GTFS",
          source_name: "fixture:rail-timetable",
          source_updated_at: "2026-01-01T00:00:00+08:00",
          freshness_status: "STALE",
          service_date_start: "2026-01-01",
          service_date_end: "2026-12-31",
          age_seconds: 22_000_000,
        },
      },
    });
    const html = renderToStaticMarkup(<TransferResults data={data} />);

    expect(html).toContain("铁路时刻数据可能已过期，请在出行前再次核对最新时刻。");
    expect(html).toContain('data-status="STALE"');
  });

  it("states clearly when the railway source timestamp is unknown", () => {
    const data = response({
      meta: {
        ...response().meta,
        railway_source: {
          provider: "CHINA_RAILWAY_GTFS",
          source_name: "fixture:rail-timetable",
          source_updated_at: null,
          freshness_status: "UNKNOWN",
          service_date_start: "2026-01-01",
          service_date_end: "2026-12-31",
          age_seconds: null,
        },
      },
    });
    const html = renderToStaticMarkup(<TransferResults data={data} />);

    expect(html).toContain("铁路数据更新时间未知，请在出行前核对最新时刻。");
    expect(html).toContain('data-status="UNKNOWN"');
  });

  it("renders partial data and null recommendation as a valid result", () => {
    const partial = response({
      recommendation: null,
      data_completeness: "PARTIAL",
      warnings: ["NO_SAFE_RECOMMENDATION"],
      candidates: [
        candidate("成都西站", "INFEASIBLE", 1, {
          reasons: ["NO_RAIL_SERVICE"],
          partial_failures: [{ code: "ROUTE_MODE_UNAVAILABLE", route_mode: "DRIVING" }],
        }),
      ],
      candidate_count: 1,
      good_candidate_count: 0,
      risky_candidate_count: 0,
      infeasible_candidate_count: 1,
    });
    const html = renderToStaticMarkup(<TransferResults data={partial} />);

    expect(html).toContain("没有找到足够稳妥");
    expect(html).toContain("部分路线数据暂时不可用");
    expect(html).toContain("查询时段内未找到合适列车");
    expect(html).toContain("驾车 / 出租车路线暂时无法获取");
    expect(html).toContain("当前查询时段内没有可行方案");
    expect(html).toContain("成都西站");
  });

  it("keeps risky candidates visible when there is no safe recommendation", () => {
    const risky = response({
      recommendation: null,
      warnings: ["NO_SAFE_RECOMMENDATION"],
      candidates: [candidate("成都南站", "RISKY", 1)],
      candidate_count: 1,
      good_candidate_count: 0,
      risky_candidate_count: 1,
      infeasible_candidate_count: 0,
    });
    const html = renderToStaticMarkup(<TransferResults data={risky} />);

    expect(html).toContain("仍有时间较紧的方案，可以查看下方候选站。");
    expect(html).toContain("成都南站");
    expect(html).not.toContain("综合比较后的第一名");
  });

  it("keeps nearby arrival airports in a separate what-if section", () => {
    const airportCandidate = candidate("成都南站", "GOOD", 1);
    const data = response({
      alternative_arrival_airports: [
        {
          arrival_hub: { id: "airport-2", name: "成都双流国际机场" },
          distance_from_requested_arrival_meters: 56_000,
          recommendation: {
            candidate_hub_id: airportCandidate.hub.id,
            candidate_hub_name: airportCandidate.hub.name,
            status: "GOOD",
            score: airportCandidate.score,
          },
          candidates: [airportCandidate],
          candidate_count: 1,
          good_candidate_count: 1,
          risky_candidate_count: 0,
          infeasible_candidate_count: 0,
          data_completeness: "COMPLETE",
          warnings: [],
          meta: response().meta,
          failure_code: null,
        },
      ],
    });
    const html = renderToStaticMarkup(<TransferResults data={data} />);

    expect(html).toContain("附近到达机场参考");
    expect(html).toContain("成都双流国际机场");
    expect(html).toContain("距当前到达机场约 56.0 公里");
    expect(html).toContain("相同时间抵达该机场");
    expect(html).toContain("成都南站");
    expect(html).toContain("airport-airport-2-candidate-1");
  });

  it("renders an airport partial result without inventing a recommendation", () => {
    const data = response({
      alternative_arrival_airports: [
        {
          arrival_hub: { id: "airport-2", name: "成都双流国际机场" },
          distance_from_requested_arrival_meters: 56_000,
          recommendation: null,
          candidates: [],
          candidate_count: 0,
          good_candidate_count: 0,
          risky_candidate_count: 0,
          infeasible_candidate_count: 0,
          data_completeness: "PARTIAL",
          warnings: ["PARTIAL_PROVIDER_DATA"],
          meta: response().meta,
          failure_code: "ROUTING_PROVIDER_UNAVAILABLE",
        },
      ],
    });
    const html = renderToStaticMarkup(<TransferResults data={data} />);

    expect(html).toContain("成都双流国际机场");
    expect(html).toContain("部分数据");
    expect(html).toContain("部分路线或铁路数据暂时不可用。");
    expect(html).not.toContain("附近替代机场推荐");
  });

  it("does not render an empty airport section when no alternatives are returned", () => {
    const html = renderToStaticMarkup(<TransferResults data={response()} />);

    expect(html).not.toContain("附近到达机场参考");
    expect(html).not.toContain("附近替代机场");
  });

  it("renders flexible date comparison metrics separately from primary candidates", () => {
    const data = response({
      flexible_date_comparison: {
        primary_date: "2026-09-18",
        days_before: 1,
        days_after: 1,
        dates: [
          {
            date: "2026-09-17",
            arrival_at: "2026-09-17T14:20:00+08:00",
            is_primary: false,
            status: "AVAILABLE",
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
            recommendation: null,
            earliest_recommended_train: null,
            candidate_count: 2,
            data_completeness: "COMPLETE",
            warnings: [],
            failure_code: null,
          },
          {
            date: "2026-09-18",
            arrival_at: arrivalAt,
            is_primary: true,
            status: "AVAILABLE",
            convenience: {
              feasible_train_count: 5,
              recommended_train_count: 3,
              earliest_recommended_departure_at: "2026-09-18T17:35:00+08:00",
              best_connection_margin_minutes: 42,
              service_span_minutes: 420,
              service_distribution_bucket_count: 3,
              service_distribution_bucket_total: 4,
              convenience_score: 78,
            },
            recommendation: response().recommendation,
            earliest_recommended_train: response().candidates[0].earliest_recommended_train,
            candidate_count: 2,
            data_completeness: "COMPLETE",
            warnings: [],
            failure_code: null,
          },
        ],
      },
    });
    const html = renderToStaticMarkup(<TransferResults data={data} />);

    expect(html).toContain("铁路衔接便利度");
    expect(html).toContain("当前选择");
    expect(html).toContain("9月17日");
    expect(html).toContain("78");
    expect(html).toContain("不代表票价、余票、客流或航班情况");
    expect(html).toContain("成都东站");
  });

  it("renders unavailable flexible dates without inventing metrics", () => {
    const data = response({
      flexible_date_comparison: {
        primary_date: "2026-09-18",
        days_before: 0,
        days_after: 1,
        dates: [
          {
            date: "2026-09-18",
            arrival_at: arrivalAt,
            is_primary: true,
            status: "AVAILABLE",
            convenience: null,
            recommendation: null,
            earliest_recommended_train: null,
            candidate_count: 0,
            data_completeness: "COMPLETE",
            warnings: [],
            failure_code: null,
          },
          {
            date: "2026-09-19",
            arrival_at: "2026-09-19T14:20:00+08:00",
            is_primary: false,
            status: "UNAVAILABLE",
            convenience: null,
            recommendation: null,
            earliest_recommended_train: null,
            candidate_count: 0,
            data_completeness: "UNAVAILABLE",
            warnings: ["PARTIAL_PROVIDER_DATA"],
            failure_code: "RAIL_DATA_OUT_OF_RANGE",
          },
        ],
      },
    });
    const html = renderToStaticMarkup(<TransferResults data={data} />);

    expect(html).toContain("数据不可用");
    expect(html).toContain("该日期超出当前铁路数据范围");
    expect(html).not.toContain("便利度 0");
  });
});
