import {
  candidateStatusLabels,
  partialFailureLabel,
  routeModeLabels,
  transferKindLabels,
} from "@/lib/transfer/presentation";
import { candidateRoleLabel, reasonFragments } from "@/lib/transfer/comparison";
import {
  formatChinaLocalDateTime,
  formatBackupGap,
  formatDistanceMeters,
  formatDuration,
  formatTrainDeparture,
} from "@/lib/transfer/format";
import type {
  BackupTrainRobustness,
  CandidateResponse,
  CandidateRouteResponse,
  TrainResponse,
} from "@/lib/transfer/types";

import RouteDetail from "./RouteDetail";
import TrainList from "./TrainList";

interface CandidateCardProps {
  candidate: CandidateResponse;
  arrivalAt: string;
  nearTieCandidateId: string | null;
  idPrefix?: string;
  arrivalHubName?: string;
}

const primaryReasonCodes = new Set([
  "NO_RAIL_SERVICE",
  "RAIL_PROVIDER_UNAVAILABLE",
  "ROUTE_MODE_UNAVAILABLE",
  "ONLY_TIGHT_CONNECTIONS",
]);

function selectedRoute(candidate: CandidateResponse): CandidateRouteResponse | null {
  if (candidate.best_mode) {
    const preferred = candidate.route_evaluations.find((item) => item.mode === candidate.best_mode);
    if (preferred) return preferred;
  }
  return candidate.route_evaluations[0] ?? null;
}

function trainSummaryText(candidate: CandidateResponse): string {
  const summary = candidate.train_summary;
  if (summary.total === 0) return "当前查询时段内未找到合适列车";
  if (summary.feasible === 0) return `没有可行车次 · 查询时段共 ${summary.total} 班`;
  return `${summary.feasible} 班可行 · ${summary.recommended} 班较稳妥`;
}

function trainDetailText(candidate: CandidateResponse): string | null {
  const total = candidate.train_summary.total;
  if (total === 0 || total === candidate.train_summary.feasible) return null;
  return `查询时段共 ${total} 班，含 ${candidate.train_summary.tight} 班时间偏紧的车次`;
}

function trainSummary(train: TrainResponse | null, arrivalAt: string): string {
  if (!train) return "暂无推荐车次";
  return `${train.train_no} · ${formatTrainDeparture(train.departure_at, arrivalAt)}`;
}

function routeUnavailableText(route: CandidateRouteResponse): string {
  if (route.errors.length === 0) return "路线暂不可用";
  return route.errors.map((failure) => partialFailureLabel(failure)).join("；");
}

function routeDescription(
  routeEvaluation: CandidateRouteResponse | null,
  fallbackRoute: CandidateResponse["route"] = null,
  transferKind: string | null | undefined = undefined,
): string {
  if (transferKind === "RAILWAY_SAME_STATION") return transferKindLabels.RAILWAY_SAME_STATION;
  const route = routeEvaluation?.route ?? fallbackRoute;
  if (!route) {
    if (transferKind === "RAILWAY_CROSS_STATION") return transferKindLabels.RAILWAY_CROSS_STATION;
    return routeEvaluation ? routeUnavailableText(routeEvaluation) : "暂无可用路线";
  }
  const facts = [
    transferKind === "RAILWAY_CROSS_STATION" ? transferKindLabels.RAILWAY_CROSS_STATION : null,
    routeModeLabels[routeEvaluation?.mode ?? route.mode],
    formatDuration(route.duration_seconds),
  ].filter((value): value is string => Boolean(value));
  const walking = formatDistanceMeters(route.walking_distance_meters);
  if (walking && (routeEvaluation?.mode ?? route.mode) === "TRANSIT") facts.push(walking);
  if ((routeEvaluation?.mode ?? route.mode) === "TRANSIT" && route.transfer_count !== null) {
    facts.push(`换乘 ${route.transfer_count} 次`);
  }
  return facts.join(" · ");
}

function BackupTrainSummary({
  robustness,
  arrivalAt,
}: {
  robustness: BackupTrainRobustness | null | undefined;
  arrivalAt: string;
}) {
  const primary = robustness?.primary_train;
  if (!primary) return null;

  const backup = robustness.backup_train;
  const gap = formatBackupGap(robustness.backup_departure_gap_seconds);
  return (
    <section className="backup-train-summary" aria-label="首选与备选车次">
      <div className="backup-train-summary-row">
        <span className="metric-label">首选车次</span>
        <strong>
          {primary.train_no} · {formatTrainDeparture(primary.departure_at, arrivalAt)}
        </strong>
      </div>
      {backup ? (
        <>
          <div className="backup-train-summary-row">
            <span className="metric-label">备选车次</span>
            <strong>
              {backup.train_no} · {formatTrainDeparture(backup.departure_at, arrivalAt)}
            </strong>
          </div>
          {backup.destination_hub?.is_nearby_alternative && (
            <p className="backup-train-destination" role="note">
              到达：{backup.destination_hub.name} · 附近替代站；未计算该站到最终目的地的接驳
            </p>
          )}
          {gap && <p className="backup-train-gap">与首选相隔 {gap}</p>}
        </>
      ) : (
        <p className="backup-train-empty">当前搜索时段内暂无后续推荐车次</p>
      )}
    </section>
  );
}

export default function CandidateCard({
  candidate,
  arrivalAt,
  nearTieCandidateId,
  idPrefix,
  arrivalHubName,
}: CandidateCardProps) {
  const bestRoute = selectedRoute(candidate);
  const route = candidate.route ?? bestRoute?.route ?? null;
  const safeTransfer = candidate.safe_transfer ?? bestRoute?.safe_transfer ?? null;
  const trains = bestRoute?.train_connections ?? [];
  const trainRobustness = candidate.train_robustness ?? bestRoute?.train_robustness ?? null;
  const primaryReasons = candidate.reasons.filter((code) => primaryReasonCodes.has(code));
  const secondaryReasons = candidate.reasons.filter((code) => !primaryReasonCodes.has(code));
  const role = candidateRoleLabel(candidate, nearTieCandidateId);
  const candidateDomId = `${idPrefix ? `${idPrefix}-` : ""}candidate-${candidate.hub.id}`;
  const transferKind = safeTransfer?.transfer_kind;

  return (
    <article
      className={`candidate-result candidate-${candidate.status.toLowerCase()}`}
      aria-labelledby={candidateDomId}
    >
      <div className="candidate-heading">
        <div className="candidate-title">
          <span className="candidate-rank" aria-label={`排名 ${candidate.rank ?? "未定"}`}>
            #{candidate.rank ?? "—"}
          </span>
          <div>
            <p className="candidate-role">{role}</p>
            <h3 id={candidateDomId}>{candidate.hub.name}</h3>
          </div>
        </div>
        <span className="status-label" data-status={candidate.status}>
          {candidateStatusLabels[candidate.status]}
        </span>
      </div>

      <div className="candidate-overview">
        <div>
          <span className="metric-label">接驳</span>
          <strong>{routeDescription(bestRoute, route, transferKind)}</strong>
        </div>
        <div>
          <span className="metric-label">计划车次</span>
          <strong>{trainSummaryText(candidate)}</strong>
          {trainDetailText(candidate) && <span>{trainDetailText(candidate)}</span>}
        </div>
        <div>
          <span className="metric-label">较早推荐</span>
          <strong>{trainSummary(candidate.earliest_recommended_train, arrivalAt)}</strong>
        </div>
      </div>

      {safeTransfer && (
        <div className="stt-summary">
          <p className="stt-recommended">
            建议选择{" "}
            <time dateTime={safeTransfer.recommended_departure_after}>
              {formatChinaLocalDateTime(safeTransfer.recommended_departure_after)}
            </time>{" "}
            之后发车的列车
          </p>
          <p className="stt-theoretical">
            理论最早可到站{" "}
            <time dateTime={safeTransfer.theoretical_ready_at}>
              {formatChinaLocalDateTime(safeTransfer.theoretical_ready_at)}
            </time>
          </p>
          <details className="stt-details">
            <summary>这个时间怎么来的？</summary>
            <p>
              {transferKind === "RAILWAY_SAME_STATION"
                ? "这是同站换乘，不需要前往另一座铁路站；建议时间包含出站准备、站内换乘和额外风险余量。"
                : transferKind === "RAILWAY_CROSS_STATION"
                  ? `这是从${arrivalHubName ?? "到达铁路站"}前往${candidate.hub.name}的跨站换乘，建议时间包含出站准备、市内交通、进站和额外风险余量。`
                  : "建议时间综合考虑下飞机或出站、托运行李、枢纽内移动、市内交通、进站和额外风险余量。"}{" "}
              这是产品规划规则，不是铁路或机场的官方保证时间。
            </p>
          </details>
        </div>
      )}

      <BackupTrainSummary robustness={trainRobustness} arrivalAt={arrivalAt} />

      {candidate.route_evaluations.length > 1 && (
        <section className="route-comparison" aria-label={`${candidate.hub.name}市内接驳方式比较`}>
          <h4>市内接驳方式</h4>
          <ul>
            {candidate.route_evaluations.map((routeEvaluation) => (
              <li
                key={routeEvaluation.mode}
                data-selected={routeEvaluation.mode === candidate.best_mode}
              >
                <div className="route-comparison-row">
                  <span>{routeModeLabels[routeEvaluation.mode]}</span>
                  <span>
                    {routeDescription(
                      routeEvaluation,
                      null,
                      routeEvaluation.safe_transfer?.transfer_kind,
                    )}
                  </span>
                  {routeEvaluation.mode === candidate.best_mode && <em>当前采用</em>}
                </div>
                {routeEvaluation.route && <RouteDetail route={routeEvaluation.route} compact />}
              </li>
            ))}
          </ul>
        </section>
      )}

      {candidate.route_evaluations.length <= 1 && route && <RouteDetail route={route} />}

      {primaryReasons.length > 0 && (
        <ul
          className="reason-list reason-list-primary"
          aria-label={`${candidate.hub.name}关键提示`}
        >
          {reasonFragments(primaryReasons).map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {secondaryReasons.length > 0 && (
        <details className="reason-details">
          <summary>为什么是这个结果？</summary>
          <ul className="reason-list" aria-label={`${candidate.hub.name}补充原因`}>
            {reasonFragments(secondaryReasons).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </details>
      )}

      {candidate.partial_failures.length > 0 && (
        <aside className="partial-candidate" role="status">
          <strong>这座站有部分数据提示</strong>
          <ul className="partial-list" aria-label={`${candidate.hub.name}数据提示`}>
            {candidate.partial_failures.map((failure, index) => (
              <li key={`${failure.code}-${failure.route_mode ?? "all"}-${index}`}>
                {partialFailureLabel(failure)}
              </li>
            ))}
          </ul>
        </aside>
      )}

      <TrainList
        candidateId={candidate.hub.id}
        arrivalAt={arrivalAt}
        trains={trains}
        primaryTrain={trainRobustness?.primary_train}
        backupTrain={trainRobustness?.backup_train}
      />
    </article>
  );
}
