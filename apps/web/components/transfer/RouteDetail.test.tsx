import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import RouteDetail from "./RouteDetail";
import type { RouteResponse } from "@/lib/transfer/types";

const route: RouteResponse = {
  mode: "TRANSIT",
  duration_seconds: 3_600,
  distance_meters: 24_000,
  walking_distance_meters: 1_200,
  transfer_count: 1,
  segments: [
    {
      mode: "TRANSIT",
      segment_type: "WALK",
      label: "步行接驳",
      instruction: "步行前往地铁站",
      duration_seconds: 600,
      distance_meters: 600,
      line_name: null,
    },
    {
      mode: "TRANSIT",
      segment_type: "SUBWAY",
      label: "地铁 18 号线",
      instruction: "乘坐地铁 18 号线前往成都东站",
      duration_seconds: 2_400,
      distance_meters: 22_800,
      line_name: "地铁 18 号线",
      vehicle_type: "地铁",
      departure_stop: "天府机场站",
      arrival_stop: "成都东站",
      stop_count: 8,
    },
  ],
  provider: "FIXTURE",
  fetched_at: "2026-09-18T06:28:00Z",
  confidence: "FIXTURE",
};

describe("RouteDetail", () => {
  it("starts collapsed and exposes an accessible control", () => {
    const html = renderToStaticMarkup(<RouteDetail route={route} />);

    expect(html).toContain("查看接驳详情");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("aria-controls=");
    expect(html).not.toContain("乘坐地铁 18 号线前往成都东站");
  });

  it("does not render a broken empty panel when segments are absent", () => {
    const html = renderToStaticMarkup(<RouteDetail route={{ ...route, segments: [] }} />);

    expect(html).toBe("");
  });
});
