import { afterEach, describe, expect, it, vi } from "vitest";

import { evaluateTransfer, TransferApiError } from "./transfer";
import type { TransferEvaluateRequest } from "../transfer/types";

const request: TransferEvaluateRequest = {
  transfer_city: "成都",
  arrival_hub: "成都天府机场",
  destination_city: "乐山",
  arrival_at: "2026-09-18T14:20:00+08:00",
  baggage: "CHECKED" as const,
  allowed_modes: ["TRANSIT", "DRIVING"],
  rail_horizon_hours: 12,
};

const response = {
  request: {},
  recommendation: null,
  candidates: [],
  candidate_count: 0,
  good_candidate_count: 0,
  risky_candidate_count: 0,
  infeasible_candidate_count: 0,
  data_completeness: "COMPLETE",
  warnings: [],
  meta: {},
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("evaluateTransfer", () => {
  it("posts the canonical request to the same-origin transfer endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(evaluateTransfer(request)).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/transfer/evaluate",
      expect.objectContaining({
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      }),
    );
  });

  it("translates the structured backend error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "ARRIVAL_HUB_AMBIGUOUS", message: "ambiguous", details: [] },
          }),
          { status: 409 },
        ),
      ),
    );

    await expect(evaluateTransfer(request)).rejects.toMatchObject({
      code: "ARRIVAL_HUB_AMBIGUOUS",
      status: 409,
    });
  });

  it("uses a retryable typed error for network failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));

    await expect(evaluateTransfer(request)).rejects.toBeInstanceOf(TransferApiError);
    await expect(evaluateTransfer(request)).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      status: 0,
    });
  });

  it("rejects an unexpected successful payload without exposing its contents", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ secret: "provider payload" })) as Response,
        ),
    );

    await expect(evaluateTransfer(request)).rejects.toMatchObject({ code: "UNEXPECTED_RESPONSE" });
  });
});
