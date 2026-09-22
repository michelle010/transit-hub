"use client";

import { useId, useState } from "react";

import { formatSegmentDistance, formatDuration } from "@/lib/transfer/format";
import { routeSegmentTitle, routeSegmentTypeLabels } from "@/lib/transfer/presentation";
import type { RouteResponse, RouteSegmentResponse } from "@/lib/transfer/types";

interface RouteDetailProps {
  route: RouteResponse;
  compact?: boolean;
}

function safeId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}

function segmentTypeLabel(segment: RouteSegmentResponse): string {
  return routeSegmentTypeLabels[segment.segment_type] ?? "其他交通";
}

function segmentStops(segment: RouteSegmentResponse): string | null {
  if (segment.departure_stop && segment.arrival_stop) {
    return `${segment.departure_stop} → ${segment.arrival_stop}`;
  }
  if (segment.departure_stop) return `从 ${segment.departure_stop} 出发`;
  if (segment.arrival_stop) return `到达 ${segment.arrival_stop}`;
  return null;
}

function segmentMeta(segment: RouteSegmentResponse): string[] {
  const facts: string[] = [];
  const duration = formatDuration(segment.duration_seconds);
  if (segment.duration_seconds !== null && segment.duration_seconds !== undefined) {
    facts.push(duration);
  }
  const distance = formatSegmentDistance(segment.distance_meters);
  if (distance) facts.push(distance);
  if (segment.stop_count !== null && segment.stop_count !== undefined) {
    facts.push(`${segment.stop_count} 站`);
  }
  return facts;
}

export default function RouteDetail({ route, compact = false }: RouteDetailProps) {
  const [expanded, setExpanded] = useState(false);
  const reactId = useId();
  if (!route.segments || route.segments.length === 0) return null;

  const detailId = `route-detail-${safeId(reactId)}`;
  return (
    <div className={`route-detail${compact ? " route-detail-compact" : ""}`}>
      <button
        className="route-detail-toggle"
        type="button"
        aria-controls={detailId}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? "收起接驳详情" : "查看接驳详情"}
      </button>
      {expanded && (
        <ol className="route-segment-list" id={detailId}>
          {route.segments.map((segment, index) => {
            const stops = segmentStops(segment);
            const meta = segmentMeta(segment);
            return (
              <li
                className="route-segment"
                key={`${segment.segment_type}-${segment.label}-${index}`}
              >
                <div className="route-segment-heading">
                  <span className="route-segment-index">{index + 1}</span>
                  <div>
                    <strong>{segmentTypeLabel(segment)}</strong>
                    <span>{routeSegmentTitle(segment)}</span>
                  </div>
                </div>
                {stops && <p className="route-segment-stops">{stops}</p>}
                {segment.instruction && (
                  <p className="route-segment-instruction">{segment.instruction}</p>
                )}
                {meta.length > 0 && <p className="route-segment-meta">{meta.join(" · ")}</p>}
                {segment.vehicle_type && segment.segment_type !== "DRIVING" && (
                  <p className="route-segment-vehicle">方式：{segment.vehicle_type}</p>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
