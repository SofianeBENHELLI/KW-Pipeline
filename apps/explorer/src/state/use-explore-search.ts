/**
 * Debounced multi-kind semantic search hook for the Explorer
 * (#319 / #313, ADR-028).
 *
 * Backs the new server-side search affordance in the header. Wraps
 * ``exploreSearch`` with a trailing-edge debounce + AbortController
 * + state machine so a parent component can render
 * ``loading / data / error / disabled / empty`` directly off the
 * returned snapshot.
 *
 * Disabled detection: when ``ApiError.code === "KW_VECTOR_SEARCH_DISABLED"``
 * (the Phase 3 503 envelope), the snapshot's ``state`` is
 * ``"disabled"`` so the UI can fall back to the existing local
 * typeahead instead of surfacing a generic error.
 *
 * Empty / whitespace queries are short-circuited locally — the route
 * rejects them with 422 and we'd rather not round-trip.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, exploreSearch } from "../api/client";
import type { ExploreSearchResponse } from "../api/types";

const DEFAULT_DEBOUNCE_MS = 300;
const DEFAULT_CHUNK_LIMIT = 10;
const DEFAULT_DOCUMENT_LIMIT = 6;
const DEFAULT_TOPIC_LIMIT = 6;

export type ExploreSearchState =
  | "idle"
  | "loading"
  | "data"
  | "empty"
  | "error"
  | "disabled";

export interface ExploreSearchSnapshot {
  state: ExploreSearchState;
  /** The query that produced the current ``data``. ``""`` when idle. */
  query: string;
  /** Populated only when ``state === "data" | "empty"``. */
  response: ExploreSearchResponse | null;
  /** Populated only when ``state === "error"``; never set for "disabled". */
  error: ApiError | string | null;
}

export interface UseExploreSearchOptions {
  apiBaseUrl?: string;
  /** Trailing-edge debounce. Default 300 ms. */
  debounceMs?: number;
  chunkLimit?: number;
  documentLimit?: number;
  topicLimit?: number;
}

const IDLE_SNAPSHOT: ExploreSearchSnapshot = {
  state: "idle",
  query: "",
  response: null,
  error: null,
};

export function useExploreSearch(
  query: string,
  options: UseExploreSearchOptions = {},
): ExploreSearchSnapshot {
  const {
    apiBaseUrl,
    debounceMs = DEFAULT_DEBOUNCE_MS,
    chunkLimit = DEFAULT_CHUNK_LIMIT,
    documentLimit = DEFAULT_DOCUMENT_LIMIT,
    topicLimit = DEFAULT_TOPIC_LIMIT,
  } = options;

  const [snapshot, setSnapshot] = useState<ExploreSearchSnapshot>(IDLE_SNAPSHOT);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const trimmed = query.trim();

    // Empty / whitespace → reset to idle and abort any in-flight call.
    if (trimmed === "") {
      abortRef.current?.abort();
      abortRef.current = null;
      setSnapshot(IDLE_SNAPSHOT);
      return;
    }

    // Trailing-edge debounce: wait ``debounceMs`` of quiet typing
    // before firing the request.
    const timeoutId = window.setTimeout(() => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Eagerly mark loading so the UI can show a spinner under the
      // input; the previous ``response`` stays so results don't flash
      // away on every keystroke.
      setSnapshot((prev) => ({
        state: "loading",
        query: trimmed,
        response: prev.state === "data" || prev.state === "empty" ? prev.response : null,
        error: null,
      }));

      exploreSearch(trimmed, {
        chunkLimit,
        documentLimit,
        topicLimit,
        baseUrl: apiBaseUrl,
        signal: controller.signal,
      })
        .then((response) => {
          if (controller.signal.aborted) return;
          const isEmpty =
            response.chunks.length === 0 &&
            response.documents.length === 0 &&
            response.topics.length === 0 &&
            response.entities.length === 0 &&
            response.relations.length === 0;
          setSnapshot({
            state: isEmpty ? "empty" : "data",
            query: trimmed,
            response,
            error: null,
          });
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          if ((err as { name?: string })?.name === "AbortError") return;

          if (err instanceof ApiError && err.code === "KW_VECTOR_SEARCH_DISABLED") {
            setSnapshot({
              state: "disabled",
              query: trimmed,
              response: null,
              error: null,
            });
            return;
          }

          setSnapshot({
            state: "error",
            query: trimmed,
            response: null,
            error:
              err instanceof ApiError
                ? err
                : err instanceof Error
                  ? err.message
                  : "Search failed.",
          });
        });
    }, debounceMs);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [query, apiBaseUrl, debounceMs, chunkLimit, documentLimit, topicLimit]);

  return snapshot;
}
