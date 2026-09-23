import {
  MAX_FLEXIBLE_DATE_OFFSET_DAYS,
  MAX_RAIL_HORIZON_HOURS,
  MIN_RAIL_HORIZON_HOURS,
} from "./config";
import type { BaggageStatus, RouteMode } from "./types";
import type { TransferFormValues } from "./form";

/** The URL is an allowlisted representation of form state, never an API response. */
export const SHARE_QUERY_KEYS = [
  "transfer_city",
  "arrival_hub",
  "destination_city",
  "arrival_date",
  "arrival_time",
  "baggage",
  "modes",
  "rail_horizon_hours",
  "include_alternative_hubs",
  "flexible_dates",
  "days_before",
  "days_after",
] as const;

const ROUTE_MODE_ORDER: readonly RouteMode[] = ["TRANSIT", "DRIVING"];
const BAGGAGE_VALUES: readonly BaggageStatus[] = ["NONE", "CHECKED", "UNKNOWN"];
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_PATTERN = /^(\d{2}):(\d{2})$/;

export type ParsedTransferQuery = Partial<TransferFormValues>;

function validDate(value: string | null): string | undefined {
  if (!value || !DATE_PATTERN.test(value)) return undefined;
  const match = DATE_PATTERN.exec(value);
  if (!match) return undefined;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const candidate = new Date(Date.UTC(year, month - 1, day));
  if (
    candidate.getUTCFullYear() !== year ||
    candidate.getUTCMonth() !== month - 1 ||
    candidate.getUTCDate() !== day
  ) {
    return undefined;
  }
  return value;
}

function validTime(value: string | null): string | undefined {
  if (!value || !TIME_PATTERN.test(value)) return undefined;
  const match = TIME_PATTERN.exec(value);
  if (!match) return undefined;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  return hours <= 23 && minutes <= 59 ? value : undefined;
}

function validInteger(value: string | null, min: number, max: number): number | undefined {
  if (!value || !/^\d+$/.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= min && parsed <= max ? parsed : undefined;
}

function validBoolean(value: string | null): boolean | undefined {
  if (value === "1") return true;
  if (value === "0") return false;
  return undefined;
}

function validText(value: string | null): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed || /[\u0000-\u001f\u007f]/.test(trimmed)) return undefined;
  return trimmed;
}

/** Serialize only the stable, user-controlled query state in deterministic order. */
export function serializeTransferQuery(values: TransferFormValues): string {
  const params = new URLSearchParams();
  params.set("transfer_city", values.transferCity.trim());
  params.set("arrival_hub", values.arrivalHub.trim());
  params.set("destination_city", values.destinationCity.trim());
  params.set("arrival_date", values.arrivalDate);
  params.set("arrival_time", values.arrivalTime);
  params.set("baggage", values.baggage);

  const modes = ROUTE_MODE_ORDER.filter((mode) => values.allowedModes.includes(mode));
  params.set("modes", modes.join(","));
  params.set("rail_horizon_hours", String(values.railHorizonHours));

  if (values.includeAlternativeHubs) params.set("include_alternative_hubs", "1");
  if (values.flexibleDatesEnabled) {
    params.set("flexible_dates", "1");
    params.set("days_before", String(values.flexibleDaysBefore));
    params.set("days_after", String(values.flexibleDaysAfter));
  }

  return params.toString();
}

/** Parse untrusted URL state into a safe partial form state. */
export function parseTransferQuery(input: string | URLSearchParams | URL): ParsedTransferQuery {
  const params =
    typeof input === "string"
      ? new URLSearchParams(input.startsWith("?") ? input.slice(1) : input)
      : input instanceof URL
        ? input.searchParams
        : input;
  const parsed: ParsedTransferQuery = {};

  const transferCity = validText(params.get("transfer_city"));
  const arrivalHub = validText(params.get("arrival_hub"));
  const destinationCity = validText(params.get("destination_city"));
  const arrivalDate = validDate(params.get("arrival_date"));
  const arrivalTime = validTime(params.get("arrival_time"));
  if (transferCity) parsed.transferCity = transferCity;
  if (arrivalHub) parsed.arrivalHub = arrivalHub;
  if (destinationCity) parsed.destinationCity = destinationCity;
  if (arrivalDate) parsed.arrivalDate = arrivalDate;
  if (arrivalTime) parsed.arrivalTime = arrivalTime;

  const baggage = params.get("baggage") as BaggageStatus | null;
  if (baggage && BAGGAGE_VALUES.includes(baggage)) parsed.baggage = baggage;

  if (params.has("modes")) {
    const modes = ROUTE_MODE_ORDER.filter((mode) =>
      (params.get("modes") ?? "")
        .split(",")
        .map((value) => value.trim().toUpperCase())
        .includes(mode),
    );
    if (modes.length > 0) parsed.allowedModes = modes;
  }

  const railHorizonHours = validInteger(
    params.get("rail_horizon_hours"),
    MIN_RAIL_HORIZON_HOURS,
    MAX_RAIL_HORIZON_HOURS,
  );
  if (railHorizonHours !== undefined) parsed.railHorizonHours = railHorizonHours;

  const includeAlternativeHubs = validBoolean(params.get("include_alternative_hubs"));
  if (includeAlternativeHubs !== undefined) {
    parsed.includeAlternativeHubs = includeAlternativeHubs;
  }

  if (params.get("flexible_dates") === "1") {
    parsed.flexibleDatesEnabled = true;
    parsed.flexibleDaysBefore =
      validInteger(params.get("days_before"), 0, MAX_FLEXIBLE_DATE_OFFSET_DAYS) ?? 1;
    parsed.flexibleDaysAfter =
      validInteger(params.get("days_after"), 0, MAX_FLEXIBLE_DATE_OFFSET_DAYS) ?? 1;
  } else if (params.get("flexible_dates") === "0") {
    parsed.flexibleDatesEnabled = false;
  }

  return parsed;
}

/** Build a browser-facing URL while preserving the current origin and pathname. */
export function buildShareUrl(values: TransferFormValues, currentUrl: string): string {
  const url = new URL(currentUrl);
  url.search = serializeTransferQuery(values);
  return url.toString();
}
