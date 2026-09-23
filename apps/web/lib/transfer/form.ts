import {
  DEFAULT_RAIL_HORIZON_HOURS,
  MAX_RAIL_HORIZON_HOURS,
  MIN_RAIL_HORIZON_HOURS,
} from "./config";
import type { BaggageStatus, RouteMode, TransferEvaluateRequest } from "./types";

export interface TransferFormValues {
  transferCity: string;
  arrivalHub: string;
  destinationCity: string;
  arrivalDate: string;
  arrivalTime: string;
  baggage: BaggageStatus;
  allowedModes: RouteMode[];
  railHorizonHours: number;
  includeAlternativeHubs: boolean;
  flexibleDatesEnabled: boolean;
  flexibleDaysBefore: number;
  flexibleDaysAfter: number;
}

export type NormalizedTransferFormValues = TransferFormValues;

export interface FormValidationErrors {
  transferCity?: string;
  arrivalHub?: string;
  destinationCity?: string;
  arrivalDate?: string;
  arrivalTime?: string;
  baggage?: string;
  allowedModes?: string;
  railHorizonHours?: string;
}

export type TransferRequestBuildResult =
  | {
      ok: true;
      request: TransferEvaluateRequest;
      values: NormalizedTransferFormValues;
    }
  | { ok: false; errors: FormValidationErrors };

export const initialTransferForm: TransferFormValues = {
  transferCity: "成都",
  arrivalHub: "成都天府机场",
  destinationCity: "乐山",
  arrivalDate: "2026-09-18",
  arrivalTime: "14:20",
  baggage: "UNKNOWN",
  allowedModes: ["TRANSIT", "DRIVING"],
  railHorizonHours: DEFAULT_RAIL_HORIZON_HOURS,
  includeAlternativeHubs: false,
  flexibleDatesEnabled: false,
  flexibleDaysBefore: 1,
  flexibleDaysAfter: 1,
};

const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const timePattern = /^\d{2}:\d{2}$/;
const baggageStatuses: BaggageStatus[] = ["NONE", "CHECKED", "UNKNOWN"];
const routeModes: RouteMode[] = ["TRANSIT", "DRIVING"];

export function serializeChinaLocalDateTime(date: string, time: string): string {
  if (!datePattern.test(date) || !timePattern.test(time)) {
    throw new Error("请输入有效的到达日期和时间");
  }

  return `${date}T${time}:00+08:00`;
}

export function buildTransferRequest(values: TransferFormValues): TransferRequestBuildResult {
  const transferCity = values.transferCity.trim();
  const arrivalHub = values.arrivalHub.trim();
  const destinationCity = values.destinationCity.trim();
  const errors: FormValidationErrors = {};

  if (!transferCity) errors.transferCity = "请输入中转城市";
  if (!arrivalHub) errors.arrivalHub = "请输入到达枢纽";
  if (!destinationCity) errors.destinationCity = "请输入目的城市";
  if (!datePattern.test(values.arrivalDate)) errors.arrivalDate = "请选择到达日期";
  if (!timePattern.test(values.arrivalTime)) errors.arrivalTime = "请选择到达时间";
  if (!baggageStatuses.includes(values.baggage)) errors.baggage = "请选择托运行李状态";
  if (
    !Number.isInteger(values.railHorizonHours) ||
    values.railHorizonHours < MIN_RAIL_HORIZON_HOURS ||
    values.railHorizonHours > MAX_RAIL_HORIZON_HOURS
  ) {
    errors.railHorizonHours = "铁路搜索时长无效";
  }

  const allowedModes = routeModes.filter((mode) => values.allowedModes.includes(mode));
  if (allowedModes.length === 0) errors.allowedModes = "至少选择一种市内交通方式";

  if (Object.keys(errors).length > 0) return { ok: false, errors };

  const normalizedValues: NormalizedTransferFormValues = {
    ...values,
    transferCity,
    arrivalHub,
    destinationCity,
    allowedModes,
  };

  return {
    ok: true,
    values: normalizedValues,
    request: {
      transfer_city: transferCity,
      arrival_hub: arrivalHub,
      destination_city: destinationCity,
      arrival_at: serializeChinaLocalDateTime(values.arrivalDate, values.arrivalTime),
      baggage: values.baggage,
      allowed_modes: allowedModes,
      rail_horizon_hours: values.railHorizonHours,
      include_alternative_hubs: values.includeAlternativeHubs,
      flexible_dates: {
        enabled: values.flexibleDatesEnabled,
        days_before: values.flexibleDaysBefore,
        days_after: values.flexibleDaysAfter,
      },
    },
  };
}
