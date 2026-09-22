import { describe, expect, it } from "vitest";

import { buildTransferRequest, initialTransferForm, serializeChinaLocalDateTime } from "./form";

describe("China-local transfer form serialization", () => {
  it("serializes a local date/time with an explicit China offset", () => {
    expect(serializeChinaLocalDateTime("2026-09-18", "14:20")).toBe("2026-09-18T14:20:00+08:00");
  });

  it("does not use the machine timezone when building the request", () => {
    const result = buildTransferRequest({
      ...initialTransferForm,
      transferCity: "  成都 ",
      arrivalHub: " 成都天府机场 ",
      destinationCity: " 乐山 ",
      arrivalDate: "2026-09-18",
      arrivalTime: "14:20",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.request.arrival_at).toBe("2026-09-18T14:20:00+08:00");
      expect(result.request.transfer_city).toBe("成都");
      expect(result.request.allowed_modes).toEqual(["TRANSIT", "DRIVING"]);
      expect(result.request.rail_horizon_hours).toBe(12);
      expect(result.request.include_alternative_hubs).toBe(false);
      expect(result.request.flexible_dates).toEqual({
        enabled: false,
        days_before: 1,
        days_after: 1,
      });
    }
  });

  it("serializes a bounded flexible-date opt-in without using browser timezone", () => {
    const result = buildTransferRequest({
      ...initialTransferForm,
      flexibleDatesEnabled: true,
      flexibleDaysBefore: 2,
      flexibleDaysAfter: 1,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.request.flexible_dates).toEqual({
        enabled: true,
        days_before: 2,
        days_after: 1,
      });
      expect(result.request.arrival_at).toBe("2026-09-18T14:20:00+08:00");
    }
  });

  it("uses the conservative UNKNOWN baggage default", () => {
    expect(initialTransferForm.baggage).toBe("UNKNOWN");
    expect(initialTransferForm.allowedModes).toEqual(["TRANSIT", "DRIVING"]);
  });

  it("rejects missing required fields and an empty route-mode selection", () => {
    const result = buildTransferRequest({
      ...initialTransferForm,
      transferCity: " ",
      arrivalHub: "",
      destinationCity: " ",
      arrivalDate: "",
      arrivalTime: "",
      allowedModes: [],
    });

    expect(result).toEqual({
      ok: false,
      errors: {
        transferCity: "请输入中转城市",
        arrivalHub: "请输入到达枢纽",
        destinationCity: "请输入目的城市",
        arrivalDate: "请选择到达日期",
        arrivalTime: "请选择到达时间",
        allowedModes: "至少选择一种市内交通方式",
      },
    });
  });
});
