import type { CitySuggestion, HubSuggestion, HubSuggestionType } from "@/lib/discovery/types";

export interface DiscoveryApiErrorInit {
  code: string;
  status: number;
  details?: unknown;
}

export class DiscoveryApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;

  constructor(message: string, init: DiscoveryApiErrorInit) {
    super(message);
    this.name = "DiscoveryApiError";
    this.code = init.code;
    this.status = init.status;
    this.details = init.details;
  }
}

interface CitySearchPayload {
  query: string;
  count: number;
  items: Array<{
    id: string;
    name_zh: string;
    name_en: string | null;
    province_name_zh: string;
    adcode: string | null;
  }>;
}

interface HubListPayload {
  city: CitySearchPayload["items"][number];
  count: number;
  items: Array<{
    id: string;
    canonical_name_zh: string;
    canonical_name_en: string | null;
    hub_type: string;
    importance_level: number;
    active: boolean;
    passenger_service: boolean;
    aliases: string[];
  }>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function errorPayload(value: unknown): { code: string; message: string; details?: unknown } {
  if (isRecord(value) && isRecord(value.error)) {
    return {
      code: typeof value.error.code === "string" ? value.error.code : "HTTP_ERROR",
      message: typeof value.error.message === "string" ? value.error.message : "建议加载失败",
      details: value.error.details,
    };
  }
  return { code: "HTTP_ERROR", message: "建议加载失败" };
}

function isCitySearchPayload(value: unknown): value is CitySearchPayload {
  return (
    isRecord(value) &&
    typeof value.query === "string" &&
    typeof value.count === "number" &&
    Array.isArray(value.items)
  );
}

function isHubListPayload(value: unknown): value is HubListPayload {
  return (
    isRecord(value) &&
    isRecord(value.city) &&
    typeof value.count === "number" &&
    Array.isArray(value.items)
  );
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function requestJson(path: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, { signal });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new DiscoveryApiError("Discovery request failed", { code: "NETWORK_ERROR", status: 0 });
  }

  const payload = await readJson(response);
  if (!response.ok) {
    const error = errorPayload(payload);
    throw new DiscoveryApiError(error.message, {
      code: error.code,
      status: response.status,
      details: error.details,
    });
  }
  return payload;
}

export async function searchCities(query: string, signal?: AbortSignal): Promise<CitySuggestion[]> {
  const payload = await requestJson(`/api/cities/search?q=${encodeURIComponent(query)}`, signal);
  if (!isCitySearchPayload(payload)) {
    throw new DiscoveryApiError("Unexpected city discovery response", {
      code: "UNEXPECTED_RESPONSE",
      status: 200,
    });
  }
  return payload.items.map((item) => ({
    id: item.id,
    name: item.name_zh,
    nameEn: item.name_en,
    provinceName: item.province_name_zh,
    adcode: item.adcode,
  }));
}

export async function getCityHubs(cityId: string, signal?: AbortSignal): Promise<HubSuggestion[]> {
  const payload = await requestJson(`/api/cities/${encodeURIComponent(cityId)}/hubs`, signal);
  if (!isHubListPayload(payload)) {
    throw new DiscoveryApiError("Unexpected hub discovery response", {
      code: "UNEXPECTED_RESPONSE",
      status: 200,
    });
  }

  return payload.items.flatMap((item) => {
    const type = item.hub_type as HubSuggestionType;
    if ((type !== "AIRPORT" && type !== "RAILWAY") || !item.active || !item.passenger_service) {
      return [];
    }
    return [
      {
        id: item.id,
        name: item.canonical_name_zh,
        nameEn: item.canonical_name_en,
        type,
        importanceLevel: item.importance_level,
        active: item.active,
        passengerService: item.passenger_service,
        aliases: item.aliases,
      },
    ];
  });
}
