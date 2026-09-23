"use client";

import { useId, useState } from "react";

import { connectionStatusLabels } from "@/lib/transfer/presentation";
import { formatNearbyDistanceMeters, formatTrainDeparture } from "@/lib/transfer/format";
import type { TrainResponse } from "@/lib/transfer/types";

interface TrainListProps {
  candidateId: string;
  arrivalAt: string;
  trains: TrainResponse[];
  primaryTrain?: TrainResponse | null;
  backupTrain?: TrainResponse | null;
}

function statusLabel(status: TrainResponse["connection_status"]): string {
  if (!status) return "状态暂无";
  return connectionStatusLabels[status];
}

function safeId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}

function sameTrain(left: TrainResponse, right: TrainResponse | null | undefined): boolean {
  if (!right) return false;
  return (
    left.train_no === right.train_no &&
    left.service_date === right.service_date &&
    left.departure_at === right.departure_at &&
    left.arrival_at === right.arrival_at &&
    left.origin_station_code === right.origin_station_code &&
    left.destination_station_code === right.destination_station_code &&
    (left.destination_hub?.id ?? null) === (right.destination_hub?.id ?? null)
  );
}

export default function TrainList({
  candidateId,
  arrivalAt,
  trains,
  primaryTrain = null,
  backupTrain = null,
}: TrainListProps) {
  const [expanded, setExpanded] = useState(false);
  const reactId = useId();
  if (trains.length === 0) return null;

  const listId = `train-list-${safeId(candidateId)}-${safeId(reactId)}`;

  return (
    <section className="train-details" aria-labelledby={`${listId}-title`}>
      <div className="train-details-heading">
        <div>
          <h4 id={`${listId}-title`}>相关计划车次</h4>
          <p>保留后端返回顺序，状态由铁路连接评估提供。</p>
        </div>
        <button
          className="train-toggle"
          type="button"
          aria-controls={listId}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "收起相关车次" : `查看相关车次（${trains.length}）`}
        </button>
      </div>

      {expanded && (
        <ul className="train-list" id={listId}>
          {trains.map((train, index) => (
            <li
              className={`train-row train-${train.connection_status?.toLowerCase() ?? "unknown"}`}
              data-status={train.connection_status ?? "UNKNOWN"}
              key={`${train.train_no}-${train.departure_at}-${index}`}
            >
              <div className="train-main">
                <strong>{train.train_no}</strong>
                {sameTrain(train, primaryTrain) && (
                  <span className="train-presentation-role" aria-label="首选车次">
                    首选
                  </span>
                )}
                {sameTrain(train, backupTrain) && (
                  <span className="train-presentation-role" aria-label="备选车次">
                    备选
                  </span>
                )}
                <span>
                  {formatTrainDeparture(train.departure_at, arrivalAt)} →{" "}
                  {formatTrainDeparture(train.arrival_at, train.departure_at)}
                </span>
                {train.destination_hub?.is_nearby_alternative && (
                  <div className="train-destination-alternative" role="note">
                    <strong>到达：{train.destination_hub.name}</strong>
                    <span>
                      附近替代站 · 距目标约{" "}
                      {formatNearbyDistanceMeters(
                        train.destination_hub.distance_from_requested_destination_meters,
                      )}
                    </span>
                    <span>尚未计算从该站到最终目的地的接驳时间。</span>
                  </div>
                )}
              </div>
              <span className="connection-label" data-status={train.connection_status ?? "UNKNOWN"}>
                {statusLabel(train.connection_status)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
