import { describe, expect, it } from "vitest";

import {
  compareChinaLocalDateTimes,
  formatBackupGap,
  formatChinaLocalDateTime,
  formatDuration,
  formatNearbyDistanceMeters,
  formatSegmentDistance,
  formatTrainDeparture,
} from "./format";

describe("transfer presentation formatting", () => {
  it("formats route durations with deterministic minute rounding", () => {
    expect(formatDuration(3005)).toBe("约 50 分钟");
    expect(formatDuration(5253)).toBe("约 1 小时 28 分钟");
    expect(formatDuration(null)).toBe("暂无路线时间");
    expect(formatSegmentDistance(600)).toBe("600 米");
    expect(formatSegmentDistance(2_000)).toBe("2.0 公里");
    expect(formatNearbyDistanceMeters(28_400)).toBe("28.4 公里");
    expect(formatNearbyDistanceMeters(800)).toBe("800 米");
  });

  it("keeps API China-local clock fields without browser timezone conversion", () => {
    expect(formatChinaLocalDateTime("2026-09-18T17:26:00+08:00")).toBe("2026-09-18 17:26");
  });

  it("makes a next-day train visible as next day", () => {
    expect(formatTrainDeparture("2026-09-19T09:10:00+08:00", "2026-09-18T14:20:00+08:00")).toBe(
      "次日 09:10",
    );
    expect(formatTrainDeparture("2026-09-18T17:26:00+08:00", "2026-09-18T14:20:00+08:00")).toBe(
      "17:26",
    );
  });

  it("formats timetable backup gaps without inventing a threshold", () => {
    expect(formatBackupGap(2_340)).toBe("39 分钟");
    expect(formatBackupGap(30)).toBe("<1 分钟");
    expect(formatBackupGap(null)).toBeNull();
  });

  it("compares China-local timestamps without using the browser timezone", () => {
    expect(
      compareChinaLocalDateTimes("2026-09-19T09:10:00+08:00", "2026-09-18T17:26:00+08:00"),
    ).toBeGreaterThan(0);
  });
});
