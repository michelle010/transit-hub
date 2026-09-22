import { describe, expect, it } from "vitest";

import {
  candidateStatusLabels,
  connectionStatusLabels,
  partialFailureLabel,
  reasonLabel,
  routeModeLabels,
  routeSegmentTypeLabels,
  transferApiErrorMessage,
  warningLabel,
} from "./presentation";

describe("transfer presentation labels", () => {
  it("localizes statuses and route modes without changing their semantics", () => {
    expect(candidateStatusLabels.RECOMMENDED).toBe("首选");
    expect(candidateStatusLabels.RISKY).toBe("时间偏紧");
    expect(candidateStatusLabels.INFEASIBLE).toBe("当前不可行");
    expect(routeModeLabels.TRANSIT).toBe("公共交通");
    expect(routeModeLabels.DRIVING).toBe("驾车 / 出租车");
    expect(routeSegmentTypeLabels.WALK).toBe("步行");
    expect(routeSegmentTypeLabels.SUBWAY).toBe("地铁");
    expect(connectionStatusLabels.SPACIOUS).toBe("余量充足");
    expect(connectionStatusLabels.SAFE).toBe("较稳妥");
    expect(connectionStatusLabels.TIGHT).toBe("时间偏紧");
    expect(connectionStatusLabels.INFEASIBLE).toBe("来不及");
  });

  it("maps partial failures and reasons to concise user-facing copy", () => {
    expect(reasonLabel("NO_RAIL_SERVICE")).toBe("查询时段内未找到合适列车");
    expect(partialFailureLabel({ code: "ROUTE_MODE_UNAVAILABLE", route_mode: "DRIVING" })).toBe(
      "驾车 / 出租车路线暂时无法获取",
    );
    expect(
      partialFailureLabel({
        code: "ROUTE_MODE_UNAVAILABLE",
        route_mode: "TRANSIT",
        availability: "UNAVAILABLE",
        reason: "NO_ROUTE",
      }),
    ).toBe("公共交通路线在当前查询时段不可用");
    expect(
      partialFailureLabel({
        code: "ROUTE_MODE_UNAVAILABLE",
        route_mode: "TRANSIT",
        availability: "PROVIDER_FAILURE",
        reason: "PROVIDER_UNAVAILABLE",
      }),
    ).toBe("公共交通路线暂时无法查询");
    expect(warningLabel("PARTIAL_PROVIDER_DATA")).toContain("部分路线数据暂时不可用");
  });

  it("does not expose provider implementation details for known errors", () => {
    expect(transferApiErrorMessage({ code: "TRANSFER_CITY_NOT_FOUND" })).toContain("中转城市");
    expect(transferApiErrorMessage({ code: "AMAP_API_KEY_MISSING" })).toBe(
      "市内交通服务暂时不可用，请稍后再试。",
    );
    expect(transferApiErrorMessage({ code: "ARRIVAL_HUB_AMBIGUOUS" })).toContain("多个地点");
    expect(transferApiErrorMessage({ code: "RAIL_DATA_OUT_OF_RANGE" })).toContain("不覆盖");
    expect(transferApiErrorMessage({ code: "ROUTING_PROVIDER_UNAVAILABLE" })).toContain("路线");
    expect(transferApiErrorMessage(new Error("secret provider payload"))).not.toContain("secret");
  });
});
