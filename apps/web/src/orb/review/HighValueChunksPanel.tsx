/**
 * HighValueChunksPanel — converged plan §C.2 "start here" surface.
 *
 * Renders the top-K chunks of a validated document version ranked
 * by composite importance score. Each row shows the chunk heading,
 * a snippet preview, the per-component contributions, and the raw
 * counts (claims / process steps / graph degree / entity mentions)
 * so the operator can see *why* the chunk ranks high.
 *
 * Mount it inside the Pipeline & FSM tab body so it sits next to
 * the confidence and FSM cards without a route change. The panel
 * never blocks — cold-start documents render an empty state, error
 * cases render an inline banner.
 */

import { useEffect, useState } from "react";
import type { ReactElement } from "react";

import { Card, CardHead, SectionH } from "../index";
import { ApiError, getDocumentHighValueChunks } from "../../api/client";
import type {
  ApiHighValueChunk,
  ApiHighValueChunksResponse,
} from "../../api/types";

export interface HighValueChunksPanelProps {
  documentId: string | null;
  /** Optional row cap. Defaults to 10 — the operator's
   *  "first screen" cohort per the §C.2 narrative. */
  limit?: number;
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: ApiHighValueChunksResponse }
  | { kind: "error"; message: string };

const _DEFAULT_LIMIT = 10;

export function HighValueChunksPanel({
  documentId,
  limit = _DEFAULT_LIMIT,
}: HighValueChunksPanelProps): ReactElement {
  const [state, setState] = useState<LoadState>(
    documentId === null ? { kind: "idle" } : { kind: "loading" },
  );

  useEffect(() => {
    if (documentId === null) {
      setState({ kind: "idle" });
      return;
    }
    const controller = new AbortController();
    setState({ kind: "loading" });
    getDocumentHighValueChunks(documentId, {
      limit,
      signal: controller.signal,
    })
      .then((data) => setState({ kind: "ready", data }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) {
          setState({ kind: "error", message: err.detail });
        } else if (err instanceof Error) {
          setState({ kind: "error", message: err.message });
        } else {
          setState({ kind: "error", message: "Failed to load chunks." });
        }
      });
    return () => controller.abort();
  }, [documentId, limit]);

  if (state.kind === "idle") {
    return (
      <Card>
        <CardHead>
          <SectionH>High-value chunks</SectionH>
        </CardHead>
        <div className="kf-hv__empty">
          Pick a document from the rail to see its top-ranked chunks.
        </div>
      </Card>
    );
  }

  if (state.kind === "loading") {
    return (
      <Card>
        <CardHead>
          <SectionH>High-value chunks</SectionH>
        </CardHead>
        <div
          className="kf-hv__empty"
          role="status"
          data-testid="kf-hv-loading"
        >
          Ranking chunks…
        </div>
      </Card>
    );
  }

  if (state.kind === "error") {
    return (
      <Card>
        <CardHead>
          <SectionH>High-value chunks</SectionH>
        </CardHead>
        <div
          className="notice danger"
          role="alert"
          data-testid="kf-hv-error"
        >
          <strong>Failed to load chunks.</strong>
          <span>{state.message}</span>
        </div>
      </Card>
    );
  }

  return <HighValueReady data={state.data} />;
}

interface HighValueReadyProps {
  data: ApiHighValueChunksResponse;
}

function HighValueReady({ data }: HighValueReadyProps): ReactElement {
  const items = data.items ?? [];
  return (
    <Card>
      <CardHead
        right={
          <span className="orb-mono kf-card-hint">
            v{data.version_number} · {items.length}/{data.total_chunks ?? 0}{" "}
            chunks
          </span>
        }
      >
        <SectionH>High-value chunks</SectionH>
      </CardHead>
      {items.length === 0 ? (
        <div
          className="kf-hv__empty"
          data-testid="kf-hv-empty"
        >
          No chunks ranked yet — extraction has not produced any chunks
          for this version. Once the semantic document lands, this panel
          will surface the top-ranked chunks.
        </div>
      ) : (
        <ol
          className="kf-hv__list"
          data-testid="kf-hv-list"
          aria-label="Top-ranked chunks"
        >
          {items.map((chunk, idx) => (
            <HighValueRow
              key={chunk.chunk_id}
              chunk={chunk}
              rank={idx + 1}
            />
          ))}
        </ol>
      )}
    </Card>
  );
}

interface HighValueRowProps {
  chunk: ApiHighValueChunk;
  rank: number;
}

function HighValueRow({ chunk, rank }: HighValueRowProps): ReactElement {
  return (
    <li
      className="kf-hv__row"
      data-testid={`kf-hv-row-${chunk.chunk_id}`}
    >
      <div className="kf-hv__row-h">
        <span className="orb-mono kf-hv__rank">#{rank}</span>
        <span
          className="kf-hv__heading"
          title={chunk.heading}
        >
          {chunk.heading || `Chunk ${chunk.chunk_id}`}
        </span>
        <span
          className="orb-mono kf-hv__score"
          data-testid={`kf-hv-score-${chunk.chunk_id}`}
        >
          {_pct(chunk.score)}
        </span>
      </div>
      {chunk.snippet ? (
        <p className="kf-hv__snippet">{chunk.snippet}</p>
      ) : null}
      <ul className="kf-hv__signals" aria-label="Signal breakdown">
        <SignalChip label="claims" value={chunk.claim_count} />
        <SignalChip label="steps" value={chunk.process_step_count} />
        <SignalChip label="degree" value={chunk.graph_degree} />
        <SignalChip label="entities" value={chunk.entity_mention_count} />
      </ul>
    </li>
  );
}

interface SignalChipProps {
  label: string;
  value: number;
}

function SignalChip({ label, value }: SignalChipProps): ReactElement {
  return (
    <li
      className={value > 0 ? "kf-hv__chip is-active" : "kf-hv__chip"}
      data-testid={`kf-hv-signal-${label}`}
    >
      <span className="kf-hv__chip-label">{label}</span>
      <span className="orb-mono kf-hv__chip-val">{value}</span>
    </li>
  );
}

function _pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}
