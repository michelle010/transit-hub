import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { transferFixture } from "../../e2e/fixtures/transfer";
import CandidateCard from "./CandidateCard";

const arrivalAt = "2026-09-18T14:20:00+08:00";

function renderCandidate(candidate: Parameters<typeof CandidateCard>[0]["candidate"]): string {
  return renderToStaticMarkup(
    <CandidateCard candidate={candidate} arrivalAt={arrivalAt} nearTieCandidateId={null} />,
  );
}

describe("CandidateCard backup-train presentation", () => {
  it("shows primary, backup and timetable gap without changing the status label", () => {
    const candidate = transferFixture("complete").candidates[0];
    const html = renderCandidate(candidate);

    expect(html).toContain("首选车次");
    expect(html).toContain("备选车次");
    expect(html).toContain("与首选相隔 65 分钟");
    expect(html).toContain('class="candidate-result candidate-recommended"');
  });

  it("states bounded no-backup and no-primary cases without claiming no trains exist", () => {
    const candidate = transferFixture("complete").candidates[0];
    const primary = candidate.train_robustness?.primary_train ?? null;
    const noBackup = renderCandidate({
      ...candidate,
      train_robustness: {
        status: "NO_BACKUP",
        primary_train: primary,
        backup_train: null,
        backup_available: false,
        backup_departure_gap_seconds: null,
      },
    });
    const noPrimary = renderCandidate({
      ...candidate,
      train_robustness: {
        status: "NO_PRIMARY_TRAIN",
        primary_train: null,
        backup_train: null,
        backup_available: false,
        backup_departure_gap_seconds: null,
      },
    });

    expect(noBackup).toContain("当前搜索时段内暂无后续推荐车次");
    expect(noBackup).not.toContain("没有其他火车");
    expect(noPrimary).not.toContain("首选车次");
    expect(noPrimary).not.toContain("备选车次");
  });

  it("keeps nearby destination metadata visible for a backup train", () => {
    const candidate = transferFixture("nearby").candidates[0];
    const html = renderCandidate(candidate);
    expect(html).toContain("峨眉山站");
    expect(html).toContain("附近替代站");
  });

  it("keeps slash-form and cross-midnight backup times in China-local presentation", () => {
    const candidate = transferFixture("complete").candidates[0];
    const robustness = candidate.train_robustness;
    if (!robustness?.backup_train) throw new Error("fixture must include a backup train");
    const html = renderCandidate({
      ...candidate,
      train_robustness: {
        ...robustness,
        backup_train: {
          ...robustness.backup_train,
          train_no: "D972/D973B",
          departure_at: "2026-09-19T09:10:00+08:00",
        },
      },
    });
    expect(html).toContain("备选车次");
    expect(html).toContain("D972/D973B");
    expect(html).toContain("次日 09:10");
  });
});

describe("CandidateCard railway transfer presentation", () => {
  it("explains same-station railway transfer without inventing a route", () => {
    const candidate = transferFixture("railway_same_station").candidates[0];
    const html = renderToStaticMarkup(
      <CandidateCard
        candidate={candidate}
        arrivalAt={arrivalAt}
        nearTieCandidateId={null}
        arrivalHubName="成都东站"
      />,
    );

    expect(html).toContain("同站换乘");
    expect(html).toContain("不需要前往另一座铁路站");
    expect(html).not.toContain("暂无可用路线");
  });

  it("explains the arrival and departure stations for a cross-station transfer", () => {
    const candidate = transferFixture("railway_cross_station").candidates[0];
    const html = renderToStaticMarkup(
      <CandidateCard
        candidate={candidate}
        arrivalAt={arrivalAt}
        nearTieCandidateId={null}
        arrivalHubName="成都站"
      />,
    );

    expect(html).toContain("需要跨站换乘");
    expect(html).toContain("成都站前往成都东站");
    expect(html).toContain("公共交通");
  });
});

describe("CandidateCard route availability presentation", () => {
  it("keeps driving visible when transit is unavailable for the query period", () => {
    const candidate = transferFixture("transit_unavailable").candidates[0];
    const html = renderCandidate(candidate);

    expect(html).toContain("公共交通路线在当前查询时段不可用");
    expect(html).toContain("驾车 / 出租车");
    expect(html).toContain("约 43 分钟");
  });

  it("uses different wording for a routing provider failure", () => {
    const candidate = transferFixture("transit_provider_failure").candidates[0];
    const html = renderCandidate(candidate);

    expect(html).toContain("公共交通路线暂时无法查询");
    expect(html).not.toContain("当前查询时段不可用");
  });

  it("does not invent a driving duration for a transit-only no-route result", () => {
    const candidate = transferFixture("transit_only_unavailable").candidates[0];
    const html = renderCandidate(candidate);

    expect(html).toContain("公共交通路线在当前查询时段不可用");
    expect(html).not.toContain("驾车 / 出租车路线");
    expect(html).not.toContain("2400");
  });
});
