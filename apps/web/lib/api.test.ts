import { afterEach, describe, expect, it, vi } from "vitest";

import { getApiHealth } from "./api";

const validHealthResponse = {
  status: "ok",
  service: "transit-hub-api",
  timezone: "Asia/Shanghai",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("getApiHealth", () => {
  it("returns the normalized API health response", async () => {
    vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(validHealthResponse), { status: 200 })),
    );

    await expect(getApiHealth()).resolves.toEqual({
      ok: true,
      data: validHealthResponse,
    });
  });

  it("reports non-success HTTP responses without exposing response details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unavailable", { status: 503 })));

    await expect(getApiHealth()).resolves.toEqual({
      ok: false,
      message: "健康检查返回 HTTP 503",
    });
  });

  it("rejects an unexpected response shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" }))),
    );

    await expect(getApiHealth()).resolves.toEqual({
      ok: false,
      message: "健康检查响应格式不匹配",
    });
  });

  it("returns a clear offline state when the API cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));

    await expect(getApiHealth()).resolves.toEqual({
      ok: false,
      message: "请确认后端服务已启动",
    });
  });
});
