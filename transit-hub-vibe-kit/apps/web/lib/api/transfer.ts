import type { TransferEvaluateRequest, TransferEvaluationResponse } from "../transfer/types";

export interface TransferApiErrorInit {
  code: string;
  status: number;
  details?: unknown;
}

export class TransferApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;

  constructor(message: string, init: TransferApiErrorInit) {
    super(message);
    this.name = "TransferApiError";
    this.code = init.code;
    this.status = init.status;
    this.details = init.details;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isTransferEvaluationResponse(value: unknown): value is TransferEvaluationResponse {
  if (!isRecord(value)) return false;
  return (
    isRecord(value.request) &&
    Array.isArray(value.candidates) &&
    typeof value.candidate_count === "number" &&
    (value.recommendation === null || isRecord(value.recommendation)) &&
    isRecord(value.meta)
  );
}

function parseErrorPayload(value: unknown): { code: string; message: string; details?: unknown } {
  if (isRecord(value) && isRecord(value.error)) {
    const code = typeof value.error.code === "string" ? value.error.code : "HTTP_ERROR";
    const message = typeof value.error.message === "string" ? value.error.message : "请求失败";
    return { code, message, details: value.error.details };
  }
  return { code: "HTTP_ERROR", message: "请求失败" };
}

export async function evaluateTransfer(
  request: TransferEvaluateRequest,
  options: { signal?: AbortSignal } = {},
): Promise<TransferEvaluationResponse> {
  let response: Response;
  try {
    response = await fetch("/api/transfer/evaluate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new TransferApiError("Network request failed", {
      code: "NETWORK_ERROR",
      status: 0,
    });
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const error = parseErrorPayload(payload);
    throw new TransferApiError(error.message, {
      code: error.code,
      status: response.status,
      details: error.details,
    });
  }

  if (!isTransferEvaluationResponse(payload)) {
    throw new TransferApiError("Unexpected transfer response", {
      code: "UNEXPECTED_RESPONSE",
      status: response.status,
    });
  }
  return payload;
}
