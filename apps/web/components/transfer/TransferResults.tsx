import { getNearTieComparison, reasonFragments } from "@/lib/transfer/comparison";
import { routeModeLabels, warningLabel } from "@/lib/transfer/presentation";
import {
  formatChinaLocalDateTime,
  formatDistanceMeters,
  formatDuration,
  formatTrainDeparture,
  formatNearbyDistanceMeters,
} from "@/lib/transfer/format";
import type {
  AlternativeArrivalAirportResponse,
  CandidateResponse,
  FlexibleDateComparisonResponse,
  FlexibleDateEvaluationResponse,
  TrainResponse,
  TransferEvaluationResponse,
} from "@/lib/transfer/types";

import CandidateCard from "./CandidateCard";

interface TransferResultsProps {
  data: TransferEvaluationResponse;
}

function trainSummary(train: TrainResponse | null, arrivalAt: string): string {
  if (!train) return "暂无推荐车次";
  return `${train.train_no} · ${formatTrainDeparture(train.departure_at, arrivalAt)}`;
}

function bestRoute(candidate: CandidateResponse) {
  if (candidate.route) return candidate.route;
  if (candidate.best_mode) {
    return (
      candidate.route_evaluations.find((item) => item.mode === candidate.best_mode)?.route ?? null
    );
  }
  return candidate.route_evaluations[0]?.route ?? null;
}

function AlternativeAirportCard({
  airport,
  arrivalAt,
}: {
  airport: AlternativeArrivalAirportResponse;
  arrivalAt: string;
}) {
  const recommendation = airport.recommendation;
  const recommendedCandidate = recommendation
    ? airport.candidates.find((candidate) => candidate.hub.id === recommendation.candidate_hub_id)
    : null;
  const route = recommendedCandidate ? bestRoute(recommendedCandidate) : null;
  const routeMode = recommendedCandidate?.best_mode ?? route?.mode;
  const distance = formatNearbyDistanceMeters(airport.distance_from_requested_arrival_meters);
  const safeTransfer = recommendedCandidate
    ? (recommendedCandidate.safe_transfer ??
      recommendedCandidate.route_evaluations.find((item) => item.mode === routeMode)?.safe_transfer)
    : null;
  const airportTitleId = `alternative-airport-${airport.arrival_hub.id}`;
  const dataCopy =
    airport.data_completeness === "PARTIAL"
      ? "部分路线或铁路数据暂时不可用。"
      : airport.data_completeness === "UNAVAILABLE"
        ? "暂时无法完成该机场的比较。"
        : null;
  const dataStatusLabel =
    airport.data_completeness === "PARTIAL"
      ? "部分数据"
      : airport.data_completeness === "UNAVAILABLE"
        ? "数据不可用"
        : "数据完整";

  return (
    <article className="alternative-airport-card" aria-labelledby={airportTitleId}>
      <div className="alternative-airport-heading">
        <div>
          <p className="alternative-airport-kicker">附近替代机场</p>
          <h4 id={airportTitleId}>{airport.arrival_hub.name}</h4>
        </div>
        <span className="alternative-airport-distance">
          {distance ? `距当前到达机场约 ${distance}` : "距离暂不可用"}
        </span>
      </div>
      <span className="alternative-airport-status" data-status={airport.data_completeness}>
        {dataStatusLabel}
      </span>
      <p className="alternative-airport-what-if">
        以下方案假设你可以在相同时间抵达该机场，仅供比较后续中转条件。
      </p>
      {recommendedCandidate && route ? (
        <div className="alternative-airport-summary">
          <div>
            <span className="metric-label">参考铁路站</span>
            <strong>{recommendedCandidate.hub.name}</strong>
          </div>
          <div>
            <span className="metric-label">接驳</span>
            <strong>
              {routeMode ? routeModeLabels[routeMode] : "市内交通"} ·{" "}
              {formatDuration(route.duration_seconds)}
            </strong>
          </div>
          <div>
            <span className="metric-label">较早推荐车次</span>
            <strong>
              {trainSummary(recommendedCandidate.earliest_recommended_train, arrivalAt)}
            </strong>
          </div>
          {safeTransfer && (
            <p>
              建议选择 {formatChinaLocalDateTime(safeTransfer.recommended_departure_after)}{" "}
              之后发车的列车。
            </p>
          )}
        </div>
      ) : (
        <p className="alternative-airport-empty">
          {dataCopy ?? "当前没有足够稳妥的参考铁路站，仍可展开查看候选。"}
        </p>
      )}
      {dataCopy && recommendedCandidate && (
        <p className="alternative-airport-data-status" role="status">
          {dataCopy}
        </p>
      )}
      {airport.candidates.length > 0 && (
        <details className="alternative-airport-details">
          <summary>查看该机场对应的候选铁路站</summary>
          <div className="candidate-list" aria-label={`${airport.arrival_hub.name}候选铁路站列表`}>
            {airport.candidates.map((candidate) => (
              <CandidateCard
                key={candidate.hub.id}
                candidate={candidate}
                arrivalAt={arrivalAt}
                nearTieCandidateId={null}
                idPrefix={`airport-${airport.arrival_hub.id}`}
                arrivalHubName={airport.arrival_hub.name}
              />
            ))}
          </div>
        </details>
      )}
    </article>
  );
}

function recommendationFacts(candidate: CandidateResponse): string[] {
  const positiveCodes = candidate.reasons.filter(
    (code) =>
      ![
        "NO_RAIL_SERVICE",
        "RAIL_PROVIDER_UNAVAILABLE",
        "ROUTE_MODE_UNAVAILABLE",
        "ONLY_TIGHT_CONNECTIONS",
      ].includes(code),
  );
  return reasonFragments(positiveCodes.length > 0 ? positiveCodes : candidate.reasons).slice(0, 3);
}

function RecommendationHero({
  candidate,
  arrivalAt,
  arrivalHubName,
}: {
  candidate: CandidateResponse;
  arrivalAt: string;
  arrivalHubName?: string;
}) {
  const route = bestRoute(candidate);
  const routeMode = candidate.best_mode ?? route?.mode;
  const routeEvaluation = candidate.best_mode
    ? candidate.route_evaluations.find((item) => item.mode === candidate.best_mode)
    : candidate.route_evaluations[0];
  const routeFacts = [
    routeMode ? routeModeLabels[routeMode] : "市内交通",
    formatDuration(route?.duration_seconds),
  ];
  const walking = formatDistanceMeters(route?.walking_distance_meters);
  if (walking && routeMode === "TRANSIT") routeFacts.push(walking);
  if (
    routeMode === "TRANSIT" &&
    route?.transfer_count !== null &&
    route?.transfer_count !== undefined
  ) {
    routeFacts.push(`换乘 ${route.transfer_count} 次`);
  }
  const safeTransfer = candidate.safe_transfer ?? routeEvaluation?.safe_transfer ?? null;
  if (safeTransfer?.transfer_kind === "RAILWAY_SAME_STATION") {
    routeFacts.splice(0, routeFacts.length, "同站换乘");
  } else if (safeTransfer?.transfer_kind === "RAILWAY_CROSS_STATION") {
    routeFacts.unshift(
      `需要跨站换乘${arrivalHubName ? `：${arrivalHubName} → ${candidate.hub.name}` : ""}`,
    );
  }
  const facts = recommendationFacts(candidate);

  return (
    <section className="recommendation-panel" aria-labelledby="recommendation-title">
      <div className="recommendation-kicker">
        <span className="recommendation-badge">首选</span>
        <span>综合比较后的第一名</span>
      </div>
      <h3 id="recommendation-title">{candidate.hub.name}</h3>
      <p className="recommendation-route">{routeFacts.join(" · ")}</p>
      {safeTransfer && (
        <p className="recommendation-threshold">
          建议选择{" "}
          <time dateTime={safeTransfer.recommended_departure_after}>
            {formatChinaLocalDateTime(safeTransfer.recommended_departure_after)}
          </time>{" "}
          之后发车的列车
        </p>
      )}
      <div className="recommendation-train">
        <span>较早的推荐车次</span>
        <strong>{trainSummary(candidate.earliest_recommended_train, arrivalAt)}</strong>
      </div>
      {facts.length > 0 && (
        <div className="recommendation-explanation">
          <h4>为什么推荐这里？</h4>
          <ul>
            {facts.map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
        </div>
      )}
      {safeTransfer && (
        <p className="recommendation-theoretical">
          理论最早可到站{" "}
          <time dateTime={safeTransfer.theoretical_ready_at}>
            {formatChinaLocalDateTime(safeTransfer.theoretical_ready_at)}
          </time>
          ，建议时间已包含额外风险余量。
        </p>
      )}
      <p className="recommendation-disclaimer">
        这是基于计划车次和预计接驳时间的中转建议，不代表实际运行或连接保证。
      </p>
    </section>
  );
}

function noRecommendationCopy(data: TransferEvaluationResponse): string {
  if (data.risky_candidate_count > 0) {
    return "仍有时间较紧的方案，可以查看下方候选站。";
  }
  if (data.candidate_count > 0 && data.infeasible_candidate_count === data.candidate_count) {
    return "当前查询时段内没有可行方案，可以尝试调整到达时间、日期或目的地。";
  }
  return "当前没有足够完整的可行数据，仍可查看下方候选站和数据提示。";
}

function formatComparisonDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return match ? `${Number(match[2])}月${Number(match[3])}日` : value;
}

function flexibleDateStatusLabel(status: FlexibleDateEvaluationResponse["status"]): string {
  if (status === "AVAILABLE") return "数据完整";
  if (status === "PARTIAL") return "部分数据";
  return "数据不可用";
}

function FlexibleDateCard({
  item,
  primaryArrivalAt,
}: {
  item: FlexibleDateEvaluationResponse;
  primaryArrivalAt: string;
}) {
  const metrics = item.convenience;
  const recommendation = item.recommendation;
  const statusLabel = flexibleDateStatusLabel(item.status);
  return (
    <article
      className="flexible-date-card"
      data-primary={item.is_primary}
      aria-labelledby={`flexible-date-${item.date}`}
    >
      <div className="flexible-date-card-heading">
        <div>
          <p className="flexible-date-kicker">{item.is_primary ? "当前选择" : "可比较日期"}</p>
          <h4 id={`flexible-date-${item.date}`}>{formatComparisonDate(item.date)}</h4>
        </div>
        <span className="flexible-date-status" data-status={item.status}>
          {statusLabel}
        </span>
      </div>
      {metrics ? (
        <>
          <div className="flexible-date-score">
            <span>铁路衔接便利度</span>
            <strong>{metrics.convenience_score}</strong>
          </div>
          <dl className="flexible-date-metrics">
            <div>
              <dt>可行车次</dt>
              <dd>{metrics.feasible_train_count} 趟</dd>
            </div>
            <div>
              <dt>推荐阈值后</dt>
              <dd>{metrics.recommended_train_count} 趟</dd>
            </div>
            <div>
              <dt>最佳衔接余量</dt>
              <dd>
                {metrics.best_connection_margin_minutes === null
                  ? "暂无"
                  : `${metrics.best_connection_margin_minutes} 分钟`}
              </dd>
            </div>
            <div>
              <dt>服务覆盖</dt>
              <dd>
                {metrics.service_distribution_bucket_count}/
                {metrics.service_distribution_bucket_total} 时段
              </dd>
            </div>
          </dl>
          <p className="flexible-date-supporting">
            {recommendation
              ? `参考铁路站：${recommendation.candidate_hub_name}`
              : "暂无安全推荐铁路站"}
            {item.earliest_recommended_train && (
              <>
                {" · 最早推荐车次："}
                {trainSummary(item.earliest_recommended_train, item.arrival_at)}
              </>
            )}
          </p>
        </>
      ) : (
        <p className="flexible-date-empty" role="status">
          {item.failure_code === "RAIL_DATA_OUT_OF_RANGE"
            ? "该日期超出当前铁路数据范围。"
            : "该日期暂时无法完成比较。"}
        </p>
      )}
      {item.status === "PARTIAL" && (
        <p className="flexible-date-supporting" role="status">
          部分路线或铁路数据暂时不可用，便利度仅基于当前可用数据。
        </p>
      )}
      {item.is_primary && (
        <p className="flexible-date-primary-note">
          当前主查询：{formatChinaLocalDateTime(primaryArrivalAt)}，主结果仍以页面上方为准。
        </p>
      )}
    </article>
  );
}

function FlexibleDateComparisonSection({
  comparison,
  primaryArrivalAt,
}: {
  comparison: FlexibleDateComparisonResponse;
  primaryArrivalAt: string;
}) {
  return (
    <section className="flexible-date-comparison" aria-labelledby="flexible-date-title">
      <div className="comparison-heading">
        <div>
          <p className="eyebrow">日期比较</p>
          <h3 id="flexible-date-title">铁路衔接便利度</h3>
        </div>
        <p>只根据可行车次数、衔接余量和车次时间分布计算，不代表票价、余票、客流或航班情况。</p>
      </div>
      <div className="flexible-date-list">
        {comparison.dates.map((item) => (
          <FlexibleDateCard key={item.date} item={item} primaryArrivalAt={primaryArrivalAt} />
        ))}
      </div>
    </section>
  );
}

function RailwaySourceNotice({
  source,
}: {
  source: TransferEvaluationResponse["meta"]["railway_source"];
}) {
  if (!source) return null;
  const updatedAt = source.source_updated_at
    ? formatChinaLocalDateTime(source.source_updated_at)
    : null;
  const coverage =
    source.service_date_start && source.service_date_end
      ? `服务日期覆盖：${source.service_date_start} 至 ${source.service_date_end}`
      : null;
  const statusMessage =
    source.freshness_status === "STALE"
      ? "铁路时刻数据可能已过期，请在出行前再次核对最新时刻。"
      : source.freshness_status === "UNKNOWN"
        ? "铁路数据更新时间未知，请在出行前核对最新时刻。"
        : null;

  return (
    <aside
      className="railway-source-notice"
      data-status={source.freshness_status}
      role={statusMessage ? "status" : undefined}
      aria-label="铁路数据来源与更新时间"
    >
      <p>
        <strong>铁路时刻数据来源：</strong>
        {source.provider}
        {source.source_name ? ` · ${source.source_name}` : ""}
      </p>
      {updatedAt ? <p>来源更新时间：{updatedAt}</p> : null}
      {statusMessage ? <p className="railway-source-warning">{statusMessage}</p> : null}
      {coverage ? <p>{coverage}</p> : null}
    </aside>
  );
}

export default function TransferResults({ data }: TransferResultsProps) {
  const recommendation = data.recommendation;
  const recommendedCandidate = recommendation
    ? data.candidates.find((candidate) => candidate.hub.id === recommendation.candidate_hub_id)
    : null;
  const nearTie = getNearTieComparison(data.candidates, recommendation);
  const warningCodes = new Set<string>(data.warnings);
  if (data.data_completeness === "PARTIAL") warningCodes.add("PARTIAL_PROVIDER_DATA");

  return (
    <section className="results-panel" aria-labelledby="results-title">
      <div className="results-heading">
        <div>
          <p className="eyebrow">评估结果</p>
          <h2 id="results-title">同城铁路站比较</h2>
        </div>
        <p className="result-count">共比较 {data.candidate_count} 个候选站</p>
      </div>

      {warningCodes.size > 0 && (
        <div className="partial-banner" role="status">
          {Array.from(warningCodes).map((code) => (
            <p key={code}>{warningLabel(code)}</p>
          ))}
        </div>
      )}

      <RailwaySourceNotice source={data.meta.railway_source} />

      {recommendation && recommendedCandidate ? (
        <RecommendationHero
          candidate={recommendedCandidate}
          arrivalAt={data.request.arrival_at}
          arrivalHubName={data.request.arrival_hub.name}
        />
      ) : (
        <div className="no-recommendation" role="status">
          <h3>当前条件下没有找到足够稳妥的中转方案。</h3>
          <p>{noRecommendationCopy(data)}</p>
        </div>
      )}

      {nearTie && (
        <aside className="near-tie-notice" role="status">
          <strong>备选也值得比较</strong>
          <p>{nearTie.message}</p>
          {nearTie.facts.length > 0 && (
            <ul>
              {nearTie.facts.map((fact) => (
                <li key={fact}>{fact}</li>
              ))}
            </ul>
          )}
        </aside>
      )}

      <section className="candidate-comparison" aria-labelledby="candidate-comparison-title">
        <div className="comparison-heading">
          <div>
            <p className="eyebrow">支持决策的细节</p>
            <h3 id="candidate-comparison-title">候选车站比较</h3>
          </div>
          <p>按照后端评估顺序展示，不代表铁路实际运行或连接保证。</p>
        </div>
        <div className="candidate-list" aria-label="候选铁路站列表">
          {data.candidates.length > 0 ? (
            data.candidates.map((candidate) => (
              <CandidateCard
                key={candidate.hub.id}
                candidate={candidate}
                arrivalAt={data.request.arrival_at}
                nearTieCandidateId={nearTie?.alternative.hub.id ?? null}
                arrivalHubName={data.request.arrival_hub.name}
              />
            ))
          ) : (
            <p className="empty-result">当前没有可展示的候选铁路站。</p>
          )}
        </div>
      </section>

      {data.flexible_date_comparison && (
        <FlexibleDateComparisonSection
          comparison={data.flexible_date_comparison}
          primaryArrivalAt={data.request.arrival_at}
        />
      )}

      {data.alternative_arrival_airports && data.alternative_arrival_airports.length > 0 && (
        <section className="alternative-airports" aria-labelledby="alternative-airports-title">
          <div className="comparison-heading">
            <div>
              <p className="eyebrow">到达侧 what-if</p>
              <h3 id="alternative-airports-title">附近到达机场参考</h3>
            </div>
            <p>机场替代方案单独比较，不会改变当前机场的主结果或铁路站排名。</p>
          </div>
          <div className="alternative-airport-list">
            {data.alternative_arrival_airports.map((airport) => (
              <AlternativeAirportCard
                key={airport.arrival_hub.id}
                airport={airport}
                arrivalAt={data.request.arrival_at}
              />
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
