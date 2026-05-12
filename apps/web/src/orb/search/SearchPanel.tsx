/**
 * SearchPanel — Knowledge Forge vector-search surface (`/kf/search`).
 *
 * Per design §6.1: single autofocus input, 300 ms debounce, mono
 * scores in the right gutter, scope chips, and a header strip with
 * count + latency + `model:voyage-… · k:N`.
 *
 * Phase-3 gating: a 503 with code `KW_VECTOR_SEARCH_DISABLED`
 * surfaces a banner that includes the operator-facing remediation
 * the backend ships verbatim. Empty `results` (Phase-3 enabled but no
 * matches) surfaces a softer "No matches" panel.
 */

import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import { OrbI } from "../index";
import "./search.css";
import {
  ApiError,
  searchKnowledgeChunks,
} from "../../api/client";
import type {
  ApiChunkSearchResponse,
  ApiChunkSearchResult,
} from "../../api/types";

const DEBOUNCE_MS = 300;

interface DisabledEnvelope {
  message: string;
  remediation: string;
}

export function SearchPanel(): ReactElement {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [data, setData] = useState<ApiChunkSearchResponse | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [topK, setTopK] = useState<number>(10);
  const [status, setStatus] = useState<
    "idle" | "loading" | "ok" | "empty" | "disabled" | "error"
  >("idle");
  const [error, setError] = useState<Error | null>(null);
  const [disabled, setDisabled] = useState<DisabledEnvelope | null>(null);

  const inputRef = useRef<HTMLInputElement | null>(null);

  // Programmatic focus on mount — avoids the autoFocus prop the
  // jsx-a11y rule flags. Same UX (cursor lands in the input) without
  // the "yanks focus across keyboard / screen-reader users" smell.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Debounced fetch driven by the trimmed input.
  useEffect(() => {
    const trimmed = q.trim();
    if (trimmed.length === 0) {
      setStatus("idle");
      setData(null);
      setLatencyMs(null);
      setError(null);
      return;
    }
    const timer = setTimeout(() => runFetch(trimmed), DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, topK]);

  const runFetch = (query: string) => {
    const controller = new AbortController();
    setStatus("loading");
    setError(null);
    setDisabled(null);
    const t0 = performance.now();
    searchKnowledgeChunks(query, { limit: topK, signal: controller.signal })
      .then((resp) => {
        setLatencyMs(performance.now() - t0);
        setData(resp);
        setStatus(resp.results.length === 0 ? "empty" : "ok");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.code === "KW_VECTOR_SEARCH_DISABLED") {
          setStatus("disabled");
          setDisabled({
            message: err.detail ?? "Vector search disabled",
            remediation:
              err.remediation ??
              "Set KW_KNOWLEDGE_LAYER_ENABLED=true and configure VOYAGE_API_KEY.",
          });
          return;
        }
        const e =
          err instanceof Error ? err : new Error(String(err));
        setStatus("error");
        setError(e);
      });
    return () => controller.abort();
  };

  return (
    <section className="kf-search" aria-label="Knowledge Forge — Search">
      <header className="kf-search__head">
        <h1 className="kf-search__title">Search</h1>
        <p className="kf-search__sub">
          Vector search over the embedded knowledge layer. Returns top-k
          chunks ranked by cosine similarity.
        </p>
      </header>

      <div className="kf-search__bar">
        <span className="kf-search__bar-icon" aria-hidden="true">
          {OrbI.search}
        </span>
        <input
          ref={inputRef}
          className="kf-search__input"
          placeholder="Type a query — debounced 300 ms…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search query"
        />
        <label className="kf-search__topk">
          <span className="orb-mono kf-search__topk-h">k=</span>
          <input
            type="number"
            min={1}
            max={50}
            value={topK}
            onChange={(e) =>
              setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 10)))
            }
            aria-label="Top-k"
          />
        </label>
      </div>

      <StatusStrip
        status={status}
        data={data}
        latencyMs={latencyMs}
        topK={topK}
      />

      <Body
        status={status}
        data={data}
        disabled={disabled}
        error={error}
        onClick={(r) => navigate(`/kf/review/${r.document_id}`)}
      />
    </section>
  );
}

function StatusStrip({
  status,
  data,
  latencyMs,
  topK,
}: {
  status: string;
  data: ApiChunkSearchResponse | null;
  latencyMs: number | null;
  topK: number;
}): ReactElement | null {
  if (status === "idle") return null;
  return (
    <div className="kf-search__strip orb-mono" data-testid="kf-search-strip">
      <span>{data?.results.length ?? 0} results</span>
      {latencyMs != null && <span> · {latencyMs.toFixed(0)} ms</span>}
      {data?.embedding_model && (
        <span>
          {" "}· model:<b>{data.embedding_model}</b>
        </span>
      )}
      <span> · k:{topK}</span>
    </div>
  );
}

function Body({
  status,
  data,
  disabled,
  error,
  onClick,
}: {
  status: string;
  data: ApiChunkSearchResponse | null;
  disabled: DisabledEnvelope | null;
  error: Error | null;
  onClick: (r: ApiChunkSearchResult) => void;
}): ReactElement {
  if (status === "idle") {
    return (
      <div className="kf-search__placeholder">
        Type a query above to search the indexed chunks.
      </div>
    );
  }
  if (status === "loading") {
    return <div className="kf-search__placeholder">Searching…</div>;
  }
  if (status === "disabled" && disabled) {
    return (
      <div
        className="kf-search__banner kf-search__banner--warn"
        role="status"
        data-testid="kf-search-disabled"
      >
        <strong>Vector search disabled.</strong>
        <p>{disabled.message}</p>
        <p className="kf-search__banner-rem orb-mono">{disabled.remediation}</p>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div
        className="kf-search__banner kf-search__banner--err"
        role="alert"
        data-testid="kf-search-error"
      >
        <strong>Search failed:</strong> {error?.message ?? "unknown error"}
      </div>
    );
  }
  if (status === "empty") {
    return (
      <div className="kf-search__placeholder">No matches for that query.</div>
    );
  }
  return (
    <ol className="kf-search__results" data-testid="kf-search-results">
      {data?.results.map((r) => (
        <li key={r.chunk_id} className="kf-search__row">
          <button
            type="button"
            className="kf-search__row-btn"
            onClick={() => onClick(r)}
            aria-label={`Open document ${r.document_id}`}
          >
            <div className="kf-search__row-head">
              <span className="orb-mono kf-search__row-id">{r.document_id}</span>
              <span aria-hidden="true">·</span>
              <span className="orb-mono kf-search__row-section">
                {r.section_id}
              </span>
              <span style={{ flex: 1 }} />
              <span className="orb-mono kf-search__row-score">
                {r.score.toFixed(3)}
              </span>
            </div>
            <p className="kf-search__row-snip">{r.snippet ?? "—"}</p>
          </button>
        </li>
      ))}
    </ol>
  );
}
