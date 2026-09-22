interface DateTimeParts {
  year: number;
  month: number;
  day: number;
  hour: string;
  minute: string;
}

function chinaDateTimeParts(value: string): DateTimeParts | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(value);
  if (!match) return null;
  return {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
    hour: match[4],
    minute: match[5],
  };
}

function calendarDayNumber(parts: DateTimeParts): number {
  return Date.UTC(parts.year, parts.month - 1, parts.day) / 86_400_000;
}

function comparableChinaDateTime(value: string): number | null {
  const parts = chinaDateTimeParts(value);
  if (!parts) return null;
  const hour = Number(parts.hour);
  const minute = Number(parts.minute);
  if (hour > 23 || minute > 59) return null;
  return Date.UTC(parts.year, parts.month - 1, parts.day, hour, minute);
}

/**
 * Compare API timestamps by their China-local wall clock fields.
 * This intentionally avoids the browser's timezone and is only used for
 * presentation comparisons; the backend remains the source of truth.
 */
export function compareChinaLocalDateTimes(
  left: string | null | undefined,
  right: string | null | undefined,
): number | null {
  if (!left || !right) return null;
  const leftValue = comparableChinaDateTime(left);
  const rightValue = comparableChinaDateTime(right);
  if (leftValue === null || rightValue === null) return null;
  return leftValue - rightValue;
}

export function formatDistanceMeters(meters: number | null | undefined): string | null {
  if (meters === null || meters === undefined || !Number.isFinite(meters) || meters < 0) {
    return null;
  }
  if (meters < 1000) return `步行约 ${Math.round(meters)} 米`;
  return `步行约 ${(meters / 1000).toFixed(1)} 公里`;
}

export function formatSegmentDistance(meters: number | null | undefined): string | null {
  if (meters === null || meters === undefined || !Number.isFinite(meters) || meters < 0) {
    return null;
  }
  if (meters < 1000) return `${Math.round(meters)} 米`;
  return `${(meters / 1000).toFixed(1)} 公里`;
}

export function formatNearbyDistanceMeters(meters: number | null | undefined): string | null {
  if (meters === null || meters === undefined || !Number.isFinite(meters) || meters < 0) {
    return null;
  }
  if (meters < 1000) return `${Math.round(meters)} 米`;
  return `${(meters / 1000).toFixed(1)} 公里`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return "暂无路线时间";
  }

  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `约 ${minutes} 分钟`;

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes === 0 ? `约 ${hours} 小时` : `约 ${hours} 小时 ${remainingMinutes} 分钟`;
}

/** Format an API datetime by reading its +08:00 clock fields without Date conversion. */
export function formatChinaLocalDateTime(value: string | null | undefined): string {
  if (!value) return "暂无时间";
  const parts = chinaDateTimeParts(value);
  if (!parts) return "时间格式不可用";
  return `${parts.year}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")} ${parts.hour}:${parts.minute}`;
}

export function formatTrainDeparture(
  value: string | null | undefined,
  arrivalAt?: string | null,
): string {
  if (!value) return "暂无推荐车次";
  const parts = chinaDateTimeParts(value);
  if (!parts) return "时间格式不可用";

  const clock = `${parts.hour}:${parts.minute}`;
  const arrivalParts = arrivalAt ? chinaDateTimeParts(arrivalAt) : null;
  if (!arrivalParts) return `${parts.month}月${parts.day}日 ${clock}`;

  const dayDelta = calendarDayNumber(parts) - calendarDayNumber(arrivalParts);
  if (dayDelta === 0) return clock;
  if (dayDelta === 1) return `次日 ${clock}`;
  return `${parts.month}月${parts.day}日 ${clock}`;
}

/** Format the scheduled gap between a primary and backup train. */
export function formatBackupGap(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  const minutes = Math.floor(seconds / 60);
  return minutes === 0 ? "<1 分钟" : `${minutes} 分钟`;
}
