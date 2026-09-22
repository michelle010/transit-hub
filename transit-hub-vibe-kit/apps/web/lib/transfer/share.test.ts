import { describe, expect, it } from "vitest";

import { initialTransferForm, type TransferFormValues } from "./form";
import { buildShareUrl, parseTransferQuery, serializeTransferQuery } from "./share";

describe("shareable transfer query", () => {
  it("serializes an allowlisted query in stable order", () => {
    const query = serializeTransferQuery({
      ...initialTransferForm,
      transferCity: "成都",
      arrivalHub: "成都天府机场",
      destinationCity: "乐山",
      arrivalDate: "2026-09-18",
      arrivalTime: "14:20",
      baggage: "CHECKED",
      allowedModes: ["DRIVING", "TRANSIT"],
      railHorizonHours: 12,
      includeAlternativeHubs: true,
      flexibleDatesEnabled: true,
      flexibleDaysBefore: 1,
      flexibleDaysAfter: 2,
    });

    expect(query).toBe(
      "transfer_city=%E6%88%90%E9%83%BD&arrival_hub=%E6%88%90%E9%83%BD%E5%A4%A9%E5%BA%9C%E6%9C%BA%E5%9C%BA&destination_city=%E4%B9%90%E5%B1%B1&arrival_date=2026-09-18&arrival_time=14%3A20&baggage=CHECKED&modes=TRANSIT%2CDRIVING&rail_horizon_hours=12&include_alternative_hubs=1&flexible_dates=1&days_before=1&days_after=2",
    );
    expect(query).not.toContain("provider_summary");
    expect(query).not.toContain("AMAP_API_KEY");
  });

  it("round-trips Chinese text and China-local form state", () => {
    const values: TransferFormValues = {
      ...initialTransferForm,
      transferCity: "成都",
      arrivalHub: "成都天府机场",
      destinationCity: "乐山",
      arrivalDate: "2026-09-18",
      arrivalTime: "14:20",
      baggage: "CHECKED" as const,
      allowedModes: ["TRANSIT", "DRIVING"],
      railHorizonHours: 12,
    };
    const parsed = parseTransferQuery(serializeTransferQuery(values));

    expect(parsed).toMatchObject({
      transferCity: "成都",
      arrivalHub: "成都天府机场",
      destinationCity: "乐山",
      arrivalDate: "2026-09-18",
      arrivalTime: "14:20",
      baggage: "CHECKED",
      allowedModes: ["TRANSIT", "DRIVING"],
      railHorizonHours: 12,
    });
  });

  it("uses safe defaults for malformed values and ignores unknown parameters", () => {
    const parsed = parseTransferQuery(
      "transfer_city=%E6%88%90%E9%83%BD&arrival_date=garbage&arrival_time=99%3A90&baggage=ALIEN&modes=INVALID&days_before=500&flexible_dates=1&foo=bar&provider=secret",
    );

    expect(parsed.transferCity).toBe("成都");
    expect(parsed.arrivalDate).toBeUndefined();
    expect(parsed.arrivalTime).toBeUndefined();
    expect(parsed.baggage).toBeUndefined();
    expect(parsed.allowedModes).toBeUndefined();
    expect(parsed.flexibleDatesEnabled).toBe(true);
    expect(parsed.flexibleDaysBefore).toBe(1);
    expect(parsed).not.toHaveProperty("foo");
    expect(parsed).not.toHaveProperty("provider");
  });

  it("ignores unknown modes but keeps a valid canonical mode", () => {
    expect(parseTransferQuery("modes=INVALID%2CDRIVING").allowedModes).toEqual(["DRIVING"]);
    expect(parseTransferQuery("modes=INVALID").allowedModes).toBeUndefined();
  });

  it("bounds horizon and flexible-date offsets", () => {
    const parsed = parseTransferQuery(
      "rail_horizon_hours=999&flexible_dates=1&days_before=4&days_after=-1",
    );

    expect(parsed.railHorizonHours).toBeUndefined();
    expect(parsed.flexibleDatesEnabled).toBe(true);
    expect(parsed.flexibleDaysBefore).toBe(1);
    expect(parsed.flexibleDaysAfter).toBe(1);
  });

  it("builds a browser-facing URL without response or provider state", () => {
    const url = buildShareUrl(
      { ...initialTransferForm, transferCity: "成都" },
      "https://example.test/?old=state#query",
    );
    const parsed = new URL(url);

    expect(parsed.origin).toBe("https://example.test");
    expect(parsed.pathname).toBe("/");
    expect(parsed.searchParams.get("transfer_city")).toBe("成都");
    expect(parsed.searchParams.get("old")).toBeNull();
    expect(parsed.searchParams.get("provider_summary")).toBeNull();
    expect(parsed.hash).toBe("#query");
  });
});
