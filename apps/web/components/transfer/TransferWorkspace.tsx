"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { evaluateTransfer, TransferApiError } from "@/lib/api/transfer";
import Autocomplete from "@/components/discovery/Autocomplete";
import type { CitySuggestion, HubSuggestion } from "@/lib/discovery/types";
import { useCitySuggestions, useHubSuggestions } from "@/lib/discovery/useDiscoverySuggestions";
import { DEFAULT_RAIL_HORIZON_HOURS } from "@/lib/transfer/config";
import {
  buildTransferRequest,
  initialTransferForm,
  type FormValidationErrors,
  type TransferFormValues,
} from "@/lib/transfer/form";
import { buildShareUrl, parseTransferQuery } from "@/lib/transfer/share";
import {
  baggageLabels,
  hubTypeLabels,
  routeModeLabels,
  transferApiErrorMessage,
} from "@/lib/transfer/presentation";
import type { RouteMode, TransferEvaluationResponse } from "@/lib/transfer/types";

import TransferResults from "./TransferResults";

type EvaluationState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: TransferEvaluationResponse }
  | { status: "error"; error: unknown };

const routeModeOptions: RouteMode[] = ["TRANSIT", "DRIVING"];

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function firstValidationMessage(errors: FormValidationErrors): string | null {
  return Object.values(errors).find((message): message is string => Boolean(message)) ?? null;
}

export default function TransferWorkspace() {
  const [values, setValues] = useState<TransferFormValues>(initialTransferForm);
  const [transferCitySelection, setTransferCitySelection] = useState<CitySuggestion | null>(null);
  const [destinationCitySelection, setDestinationCitySelection] = useState<CitySuggestion | null>(
    null,
  );
  const [arrivalHubSelection, setArrivalHubSelection] = useState<HubSuggestion | null>(null);
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const [state, setState] = useState<EvaluationState>({ status: "idle" });
  const [lastSubmittedValues, setLastSubmittedValues] = useState<TransferFormValues | null>(null);
  const [shareStatus, setShareStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const citySuggestions = useCitySuggestions(values.transferCity);
  const destinationSuggestions = useCitySuggestions(values.destinationCity);
  const hubSuggestions = useHubSuggestions(transferCitySelection?.id ?? null, values.arrivalHub);

  useEffect(() => {
    function hydrateFromLocation() {
      const parsed = parseTransferQuery(window.location.search);
      abortRef.current?.abort();
      abortRef.current = null;
      setValues({ ...initialTransferForm, ...parsed });
      setTransferCitySelection(null);
      setDestinationCitySelection(null);
      setArrivalHubSelection(null);
      setValidationMessage(null);
      setShareStatus(null);
      setState({ status: "idle" });
      setLastSubmittedValues(null);
    }

    hydrateFromLocation();
    window.addEventListener("popstate", hydrateFromLocation);
    return () => {
      window.removeEventListener("popstate", hydrateFromLocation);
      abortRef.current?.abort();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state.status === "loading") return;

    const built = buildTransferRequest(values);
    if (!built.ok) {
      setValidationMessage(firstValidationMessage(built.errors));
      return;
    }

    setValidationMessage(null);
    setValues(built.values);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: "loading" });

    try {
      const data = await evaluateTransfer(built.request, { signal: controller.signal });
      if (!controller.signal.aborted) {
        setState({ status: "success", data });
        setLastSubmittedValues(built.values);
        setShareStatus(null);
        const shareUrl = buildShareUrl(built.values, window.location.href);
        window.history.replaceState(window.history.state, "", shareUrl);
      }
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) return;
      setState({ status: "error", error });
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }

  async function copyShareLink() {
    if (!lastSubmittedValues) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(buildShareUrl(lastSubmittedValues, window.location.href));
      setShareStatus("查询链接已复制");
    } catch {
      setShareStatus("无法自动复制，请从浏览器地址栏复制当前链接。");
    }
  }

  function updateValue<Key extends keyof TransferFormValues>(
    key: Key,
    value: TransferFormValues[Key],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function updateTransferCity(value: string) {
    updateValue("transferCity", value);
    if (transferCitySelection?.name !== value) setTransferCitySelection(null);
    setArrivalHubSelection(null);
  }

  function updateDestinationCity(value: string) {
    updateValue("destinationCity", value);
    if (destinationCitySelection?.name !== value) setDestinationCitySelection(null);
  }

  function updateArrivalHub(value: string) {
    updateValue("arrivalHub", value);
    if (arrivalHubSelection?.name !== value) setArrivalHubSelection(null);
  }

  function selectTransferCity(option: CitySuggestion) {
    setTransferCitySelection(option);
    setArrivalHubSelection(null);
    updateValue("transferCity", option.name);
    citySuggestions.close();
  }

  function selectDestinationCity(option: CitySuggestion) {
    setDestinationCitySelection(option);
    updateValue("destinationCity", option.name);
    destinationSuggestions.close();
  }

  function selectArrivalHub(option: HubSuggestion) {
    setArrivalHubSelection(option);
    updateValue("arrivalHub", option.name);
    hubSuggestions.close();
  }

  function toggleMode(mode: RouteMode) {
    const allowedModes = values.allowedModes.includes(mode)
      ? values.allowedModes.filter((current) => current !== mode)
      : [...values.allowedModes, mode];
    updateValue("allowedModes", allowedModes);
    if (allowedModes.length > 0 && validationMessage === "至少选择一种市内交通方式") {
      setValidationMessage(null);
    }
  }

  return (
    <section
      className="workspace-panel"
      aria-labelledby="query-title"
      aria-busy={state.status === "loading"}
    >
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">开始规划</p>
          <h2 id="query-title">输入你的到达信息</h2>
        </div>
        <div className="workspace-heading-actions">
          <p className="workspace-hint">我们会比较同城铁路客运站、接驳时间和计划车次。</p>
          <button
            className="share-button"
            type="button"
            onClick={copyShareLink}
            disabled={!lastSubmittedValues}
          >
            复制查询链接
          </button>
          {shareStatus && (
            <p className="share-status" role="status" aria-live="polite">
              {shareStatus}
            </p>
          )}
        </div>
      </div>

      <form className="transfer-form" onSubmit={submit} noValidate>
        <div className="form-grid">
          <Autocomplete
            id="transfer-city"
            name="transfer_city"
            label="中转城市"
            value={values.transferCity}
            placeholder="例如：成都"
            options={citySuggestions.suggestions.map((city) => ({
              id: city.id,
              label: city.name,
              meta: city.provinceName,
            }))}
            isLoading={citySuggestions.isLoading}
            error={citySuggestions.error}
            isOpen={citySuggestions.isOpen}
            activeIndex={citySuggestions.activeIndex}
            onChange={updateTransferCity}
            onOpen={citySuggestions.open}
            onClose={citySuggestions.close}
            onActiveIndexChange={citySuggestions.setActiveIndex}
            onSelect={(option) => {
              const city = citySuggestions.suggestions.find((item) => item.id === option.id);
              if (city) selectTransferCity(city);
            }}
          />

          <Autocomplete
            id="arrival-hub"
            name="arrival_hub"
            label="到达枢纽"
            value={values.arrivalHub}
            placeholder="例如：成都天府机场"
            options={hubSuggestions.suggestions.map((hub) => ({
              id: hub.id,
              label: hub.name,
              meta: hubTypeLabels[hub.type],
            }))}
            isLoading={hubSuggestions.isLoading}
            error={hubSuggestions.error}
            isOpen={hubSuggestions.isOpen}
            activeIndex={hubSuggestions.activeIndex}
            onChange={updateArrivalHub}
            onOpen={hubSuggestions.open}
            onClose={hubSuggestions.close}
            onActiveIndexChange={hubSuggestions.setActiveIndex}
            onSelect={(option) => {
              const hub = hubSuggestions.suggestions.find((item) => item.id === option.id);
              if (hub) selectArrivalHub(hub);
            }}
            hint={transferCitySelection ? undefined : "先选择中转城市，可加载该城市的枢纽建议。"}
          />

          <Autocomplete
            id="destination-city"
            name="destination_city"
            label="目的城市"
            value={values.destinationCity}
            placeholder="例如：乐山"
            options={destinationSuggestions.suggestions.map((city) => ({
              id: city.id,
              label: city.name,
              meta: city.provinceName,
            }))}
            isLoading={destinationSuggestions.isLoading}
            error={destinationSuggestions.error}
            isOpen={destinationSuggestions.isOpen}
            activeIndex={destinationSuggestions.activeIndex}
            onChange={updateDestinationCity}
            onOpen={destinationSuggestions.open}
            onClose={destinationSuggestions.close}
            onActiveIndexChange={destinationSuggestions.setActiveIndex}
            onSelect={(option) => {
              const city = destinationSuggestions.suggestions.find((item) => item.id === option.id);
              if (city) selectDestinationCity(city);
            }}
          />

          <label className="field">
            <span>到达日期</span>
            <input
              name="arrival_date"
              type="date"
              value={values.arrivalDate}
              onChange={(event) => updateValue("arrivalDate", event.currentTarget.value)}
            />
          </label>

          <label className="field">
            <span>到达时间</span>
            <input
              name="arrival_time"
              type="time"
              value={values.arrivalTime}
              onChange={(event) => updateValue("arrivalTime", event.currentTarget.value)}
            />
          </label>

          <label className="field">
            <span>托运行李</span>
            <select
              name="baggage"
              value={values.baggage}
              onChange={(event) =>
                updateValue("baggage", event.currentTarget.value as TransferFormValues["baggage"])
              }
            >
              {(Object.keys(baggageLabels) as TransferFormValues["baggage"][]).map((baggage) => (
                <option key={baggage} value={baggage}>
                  {baggageLabels[baggage]}
                </option>
              ))}
            </select>
          </label>
        </div>

        <fieldset className="mode-fieldset">
          <legend>市内交通方式</legend>
          <div className="mode-options">
            {routeModeOptions.map((mode) => (
              <label className="mode-option" key={mode}>
                <input
                  type="checkbox"
                  name="allowed_modes"
                  value={mode}
                  checked={values.allowedModes.includes(mode)}
                  onChange={() => toggleMode(mode)}
                />
                <span>{routeModeLabels[mode]}</span>
              </label>
            ))}
          </div>
          <p className="field-help">
            至少选择一种方式；铁路搜索默认覆盖到达后 {DEFAULT_RAIL_HORIZON_HOURS} 小时。
          </p>
        </fieldset>

        <label className="mode-option nearby-option">
          <input
            type="checkbox"
            name="include_alternative_hubs"
            checked={values.includeAlternativeHubs}
            onChange={(event) => updateValue("includeAlternativeHubs", event.currentTarget.checked)}
          />
          <span>显示附近替代交通枢纽</span>
        </label>
        <p className="field-help">
          包括目的地附近铁路站，以及可作为到达点参考的附近机场。替代机场按相同到达时间作 what-if
          比较。
        </p>

        <fieldset className="flexible-date-fieldset">
          <legend>日期比较</legend>
          <label className="mode-option flexible-date-toggle">
            <input
              type="checkbox"
              name="flexible_dates_enabled"
              checked={values.flexibleDatesEnabled}
              onChange={(event) => updateValue("flexibleDatesEnabled", event.currentTarget.checked)}
            />
            <span>比较前后日期</span>
          </label>
          {values.flexibleDatesEnabled && (
            <div className="flexible-date-controls">
              <label className="field flexible-date-select">
                <span>提前天数</span>
                <select
                  name="flexible_days_before"
                  value={values.flexibleDaysBefore}
                  onChange={(event) =>
                    updateValue("flexibleDaysBefore", Number(event.currentTarget.value))
                  }
                >
                  {[0, 1, 2, 3].map((days) => (
                    <option key={days} value={days}>
                      {days} 天
                    </option>
                  ))}
                </select>
              </label>
              <label className="field flexible-date-select">
                <span>延后天数</span>
                <select
                  name="flexible_days_after"
                  value={values.flexibleDaysAfter}
                  onChange={(event) =>
                    updateValue("flexibleDaysAfter", Number(event.currentTarget.value))
                  }
                >
                  {[0, 1, 2, 3].map((days) => (
                    <option key={days} value={days}>
                      {days} 天
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
          <p className="field-help">
            比较同一到达时间在相邻日期的铁路衔接便利度，最多比较前后各 3 天。
          </p>
        </fieldset>

        {validationMessage && (
          <p className="inline-error" role="alert">
            {validationMessage}
          </p>
        )}

        {state.status === "error" && (
          <div className="api-error" role="alert">
            <p>{transferApiErrorMessage(state.error)}</p>
            {state.error instanceof TransferApiError &&
              state.error.code === "ARRIVAL_HUB_AMBIGUOUS" && (
                <p className="error-hint">请在到达枢纽中补充机场或车站的完整名称。</p>
              )}
          </div>
        )}

        <button className="submit-button" type="submit" disabled={state.status === "loading"}>
          {state.status === "loading" ? "正在比较候选车站…" : "开始评估"}
        </button>
        {state.status === "loading" && (
          <p className="loading-note" role="status" aria-live="polite">
            正在比较候选车站、交通时间和列车时刻…
          </p>
        )}
      </form>

      {state.status === "success" && <TransferResults data={state.data} />}
    </section>
  );
}
