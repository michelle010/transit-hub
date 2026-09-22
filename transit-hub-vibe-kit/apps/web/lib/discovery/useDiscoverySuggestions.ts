import { useCallback, useEffect, useRef, useState } from "react";

import { getCityHubs, searchCities } from "@/lib/api/discovery";
import type { CitySuggestion, HubSuggestion } from "./types";

export const DISCOVERY_DEBOUNCE_MS = 200;
export const MIN_DISCOVERY_QUERY_LENGTH = 1;
export const MAX_DISCOVERY_SUGGESTIONS = 8;

export interface DiscoveryState<T> {
  suggestions: T[];
  isLoading: boolean;
  error: string | null;
  isOpen: boolean;
  activeIndex: number;
  open: () => void;
  close: () => void;
  setActiveIndex: (index: number) => void;
}

interface UseDiscoverySuggestionsOptions<T> {
  query: string;
  load: (query: string, signal: AbortSignal) => Promise<T[]>;
  contextKey?: string;
  enabled?: boolean;
  minQueryLength?: number;
  debounceMs?: number;
}

const DISCOVERY_ERROR_MESSAGE = "暂时无法加载建议，仍可直接输入名称。";

export function useDiscoverySuggestions<T>({
  query,
  load,
  contextKey = "default",
  enabled = true,
  minQueryLength = MIN_DISCOVERY_QUERY_LENGTH,
  debounceMs = DISCOVERY_DEBOUNCE_MS,
}: UseDiscoverySuggestionsOptions<T>): DiscoveryState<T> {
  const [suggestions, setSuggestions] = useState<T[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndexState] = useState(-1);
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // These resets intentionally invalidate the previous query/context before
  // the debounced request starts, so stale options never remain selectable.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    abortRef.current?.abort();

    const normalized = query.trim();
    setSuggestions([]);
    setActiveIndexState(-1);
    setError(null);
    setIsOpen(false);

    if (!enabled || normalized.length < minQueryLength) {
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setIsLoading(true);
    const timer = window.setTimeout(() => {
      void load(normalized, controller.signal)
        .then((nextSuggestions) => {
          if (controller.signal.aborted || generationRef.current !== generation) return;
          setSuggestions(nextSuggestions);
          setIsOpen(true);
          setIsLoading(false);
        })
        .catch((reason: unknown) => {
          if (
            controller.signal.aborted ||
            generationRef.current !== generation ||
            (reason instanceof Error && reason.name === "AbortError")
          ) {
            return;
          }
          setSuggestions([]);
          setIsOpen(true);
          setIsLoading(false);
          setError(DISCOVERY_ERROR_MESSAGE);
        });
    }, debounceMs);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (abortRef.current === controller) abortRef.current = null;
    };
  }, [contextKey, debounceMs, enabled, load, minQueryLength, query]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Abort any request that is still in flight when this hook unmounts.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const open = useCallback(() => {
    if (suggestions.length > 0 || query.trim().length >= minQueryLength) {
      setIsOpen(true);
    }
  }, [minQueryLength, query, suggestions.length]);

  const close = useCallback(() => {
    setIsOpen(false);
    setActiveIndexState(-1);
  }, []);

  const setActiveIndex = useCallback((index: number) => {
    setActiveIndexState(index);
  }, []);

  return {
    suggestions,
    isLoading,
    error,
    isOpen,
    activeIndex,
    open,
    close,
    setActiveIndex,
  };
}

export function filterHubSuggestions(query: string, hubs: HubSuggestion[]): HubSuggestion[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return hubs.slice(0, MAX_DISCOVERY_SUGGESTIONS);

  return hubs
    .filter((hub) => {
      const names = [hub.name, hub.nameEn ?? "", ...hub.aliases];
      return names.some((name) => name.toLowerCase().includes(normalized));
    })
    .slice(0, MAX_DISCOVERY_SUGGESTIONS);
}

export function useCitySuggestions(query: string): DiscoveryState<CitySuggestion> {
  return useDiscoverySuggestions({
    query,
    load: searchCities,
  });
}

export function useHubSuggestions(
  cityId: string | null,
  query: string,
): DiscoveryState<HubSuggestion> {
  const [hubs, setHubs] = useState<HubSuggestion[]>([]);
  const [suggestions, setSuggestions] = useState<HubSuggestion[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndexState] = useState(-1);
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    abortRef.current?.abort();
    setHubs([]);
    setSuggestions([]);
    setError(null);
    setIsOpen(false);
    setActiveIndexState(-1);

    if (!cityId) {
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setIsLoading(true);
    void getCityHubs(cityId, controller.signal)
      .then((nextHubs) => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        setHubs(nextHubs);
        setIsLoading(false);
      })
      .catch((reason: unknown) => {
        if (
          controller.signal.aborted ||
          generationRef.current !== generation ||
          (reason instanceof Error && reason.name === "AbortError")
        ) {
          return;
        }
        setHubs([]);
        setSuggestions([]);
        setIsLoading(false);
        setIsOpen(true);
        setError(DISCOVERY_ERROR_MESSAGE);
      });

    return () => {
      controller.abort();
      if (abortRef.current === controller) abortRef.current = null;
    };
  }, [cityId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const nextSuggestions = filterHubSuggestions(query, hubs);
      setSuggestions(nextSuggestions);
      if (query.trim()) setIsOpen(true);
      setActiveIndexState(-1);
    }, DISCOVERY_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [hubs, query]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const open = useCallback(() => {
    if (cityId && (suggestions.length > 0 || hubs.length > 0)) setIsOpen(true);
  }, [cityId, hubs.length, suggestions.length]);

  const close = useCallback(() => {
    setIsOpen(false);
    setActiveIndexState(-1);
  }, []);

  const setActiveIndex = useCallback((index: number) => {
    setActiveIndexState(index);
  }, []);

  return {
    suggestions,
    isLoading,
    error,
    isOpen,
    activeIndex,
    open,
    close,
    setActiveIndex,
  };
}
