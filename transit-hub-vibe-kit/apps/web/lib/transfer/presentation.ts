import type { TransferApiError } from "../api/transfer";
import type {
  BaggageStatus,
  CandidateStatus,
  PartialFailureResponse,
  RouteMode,
  RouteSegmentResponse,
  RouteSegmentType,
  TransferKind,
} from "./types";
import type { HubSuggestionType } from "@/lib/discovery/types";

export const baggageLabels: Record<BaggageStatus, string> = {
  NONE: "无托运行李",
  CHECKED: "有托运行李",
  UNKNOWN: "不确定",
};

export const routeModeLabels: Record<RouteMode, string> = {
  TRANSIT: "公共交通",
  DRIVING: "驾车 / 出租车",
};

export const transferKindLabels: Record<TransferKind, string> = {
  AIRPORT_TO_RAILWAY: "机场到铁路站",
  RAILWAY_SAME_STATION: "同站换乘",
  RAILWAY_CROSS_STATION: "需要跨站换乘",
};

export const routeSegmentTypeLabels: Record<RouteSegmentType, string> = {
  TRANSIT: "交通接驳",
  WALK: "步行",
  BUS: "公交",
  SUBWAY: "地铁",
  RAILWAY: "铁路接驳",
  TAXI: "出租车",
  DRIVING: "驾车",
};

export function routeSegmentTitle(segment: RouteSegmentResponse): string {
  return (
    segment.line_name || segment.label || routeSegmentTypeLabels[segment.segment_type] || "其他交通"
  );
}

export const hubTypeLabels: Record<HubSuggestionType, string> = {
  AIRPORT: "机场",
  RAILWAY: "火车站",
};

export const candidateStatusLabels: Record<CandidateStatus, string> = {
  RECOMMENDED: "首选",
  GOOD: "可行",
  RISKY: "时间偏紧",
  INFEASIBLE: "当前不可行",
};

export const connectionStatusLabels = {
  SPACIOUS: "余量充足",
  SAFE: "较稳妥",
  TIGHT: "时间偏紧",
  INFEASIBLE: "来不及",
} as const;

const reasonLabels: Record<string, string> = {
  MANY_FEASIBLE_TRAINS: "可选车次较多",
  SHORT_TRANSFER: "前往车站用时较短",
  GOOD_SAFE_MARGIN: "中转余量较充足",
  LOW_TRANSFER_COMPLEXITY: "市内接驳较简单",
  NO_RAIL_SERVICE: "查询时段内未找到合适列车",
  ONLY_TIGHT_CONNECTIONS: "可行车次时间较紧",
  LONG_TRANSFER: "前往车站耗时较长",
  HIGH_TRANSFER_COMPLEXITY: "市内换乘较复杂",
  ROUTE_MODE_UNAVAILABLE: "部分交通方式路线暂不可用",
  RAIL_PROVIDER_UNAVAILABLE: "铁路时刻数据暂时不可用",
};

export function reasonLabel(code: string): string {
  return reasonLabels[code] ?? "当前结果包含该站的补充说明";
}

export function partialFailureLabel(failure: PartialFailureResponse): string {
  if (failure.code === "ROUTE_MODE_UNAVAILABLE" && failure.route_mode) {
    if (failure.reason === "NO_ROUTE" || failure.availability === "UNAVAILABLE") {
      return `${routeModeLabels[failure.route_mode]}路线在当前查询时段不可用`;
    }
    if (failure.availability === "PROVIDER_FAILURE") {
      return `${routeModeLabels[failure.route_mode]}路线暂时无法查询`;
    }
    if (failure.availability === "QUOTA_OR_BUDGET_FAILURE") {
      return `${routeModeLabels[failure.route_mode]}路线请求达到服务限制，暂时无法查询`;
    }
    // Keep the legacy wording for older responses that do not yet carry the
    // additive route-availability metadata.
    return `${routeModeLabels[failure.route_mode]}路线暂时无法获取`;
  }
  if (failure.code === "RAIL_PROVIDER_UNAVAILABLE") return "铁路时刻数据暂时不可用";
  return reasonLabel(failure.code);
}

const errorMessages: Record<string, string> = {
  TRANSFER_CITY_NOT_FOUND: "没有找到这个中转城市，请检查输入。",
  DESTINATION_CITY_NOT_FOUND: "没有找到这个目的城市，请检查输入。",
  ARRIVAL_HUB_NOT_FOUND: "没有找到这个到达枢纽，请尝试完整名称或常用名称。",
  ARRIVAL_HUB_AMBIGUOUS: "这个枢纽名称对应多个地点，请输入更完整的名称。",
  ARRIVAL_HUB_NOT_AVAILABLE: "这个到达枢纽暂不支持中转规划，请更换枢纽。",
  NO_CANDIDATE_STATIONS: "这个城市暂时没有可比较的铁路客运站。",
  RAIL_DATA_OUT_OF_RANGE: "当前铁路时刻数据暂不覆盖这个日期。",
  RAIL_DATA_NOT_LOADED: "铁路时刻数据暂时不可用，请稍后再试。",
  ROUTING_PROVIDER_UNAVAILABLE: "市内交通路线暂时无法查询，请稍后再试。",
  AMAP_API_KEY_MISSING: "市内交通服务暂时不可用，请稍后再试。",
  DATABASE_UNAVAILABLE: "中转数据暂时无法读取，请稍后再试。",
  TRANSFER_DEPENDENCY_UNAVAILABLE: "中转数据服务暂时不可用，请稍后再试。",
  COORDINATE_SYSTEM_UNSUPPORTED: "部分枢纽坐标暂不支持路线查询，请稍后再试。",
  VALIDATION_ERROR: "请检查输入信息后再试。",
  INTERNAL_ERROR: "服务暂时遇到问题，请稍后重试。",
  UNEXPECTED_RESPONSE: "服务返回了无法识别的结果，请稍后重试。",
};

export function transferApiErrorMessage(error: unknown): string {
  if (error && typeof error === "object" && "code" in error) {
    const code = (error as TransferApiError).code;
    if (typeof code === "string" && errorMessages[code]) return errorMessages[code];
  }
  return "暂时无法完成中转评估，请检查网络后重试。";
}

export function warningLabel(code: string): string {
  if (code === "PARTIAL_PROVIDER_DATA") {
    return "部分路线数据暂时不可用，以下建议基于当前可获得的数据。";
  }
  if (code === "NO_SAFE_RECOMMENDATION") {
    return "当前条件下没有足够稳妥的中转方案。";
  }
  if (code === "NO_FEASIBLE_CONNECTION") {
    return "当前查询时段内没有可行的计划车次。";
  }
  return "当前结果包含数据提示，请结合具体候选查看。";
}
