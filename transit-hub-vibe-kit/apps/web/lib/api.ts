import type { ApiHealthResponse } from "@transit-hub/shared/contracts";

type ApiHealthResult = { ok: true; data: ApiHealthResponse } | { ok: false; message: string };

function isApiHealthResponse(value: unknown): value is ApiHealthResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const response = value as Record<string, unknown>;
  return (
    response.status === "ok" &&
    response.service === "transit-hub-api" &&
    response.timezone === "Asia/Shanghai"
  );
}

export async function getApiHealth(): Promise<ApiHealthResult> {
  const baseUrl = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  const healthUrl = new URL("/health", baseUrl);

  try {
    const response = await fetch(healthUrl, {
      cache: "no-store",
      signal: AbortSignal.timeout(2_500),
    });

    if (!response.ok) {
      return { ok: false, message: `健康检查返回 HTTP ${response.status}` };
    }

    const payload: unknown = await response.json();
    if (!isApiHealthResponse(payload)) {
      return { ok: false, message: "健康检查响应格式不匹配" };
    }

    return { ok: true, data: payload };
  } catch {
    return { ok: false, message: "请确认 FastAPI 已在 127.0.0.1:8000 启动" };
  }
}
