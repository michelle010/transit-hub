import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import TrainList from "./TrainList";

const trains = [
  {
    train_no: "C5771/C5774",
    train_type: "C",
    train_class: "C",
    service_date: "2026-09-18",
    origin_station_code: "A",
    origin_station_name: "成都东站",
    destination_station_code: "B",
    destination_station_name: "乐山站",
    departure_at: "2026-09-19T09:10:00+08:00",
    arrival_at: "2026-09-19T09:56:00+08:00",
    duration_seconds: 2_760,
    connection_status: "SPACIOUS" as const,
    reason_codes: [],
  },
];

describe("TrainList", () => {
  it("starts collapsed and exposes an accessible expansion control", () => {
    const html = renderToStaticMarkup(
      <TrainList
        candidateId="chengdu-east"
        arrivalAt="2026-09-18T14:20:00+08:00"
        trains={trains}
      />,
    );

    expect(html).toContain("查看相关车次（1）");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("C5771/C5774");
  });

  it("keeps primary destinations visually unchanged", () => {
    const html = renderToStaticMarkup(
      <TrainList
        candidateId="chengdu-east"
        arrivalAt="2026-09-18T14:20:00+08:00"
        trains={trains}
      />,
    );

    expect(html).not.toContain("附近替代站");
    expect(html).not.toContain("尚未计算从该站到最终目的地的接驳时间");
  });
});
