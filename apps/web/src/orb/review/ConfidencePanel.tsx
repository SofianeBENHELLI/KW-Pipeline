/**
 * ConfidencePanel — converged plan §C.1 reviewer dashboard.
 *
 * Reads ``GET /documents/{id}/confidence`` for the active document
 * and renders the composite score + per-signal bars + the HITL
 * routing outcome. The data has been produced on every NEEDS_REVIEW
 * transition since EPIC-A slice 1 (ADR-023); this panel is just
 * the operator-facing surface over it.
 *
 * Mount it inside the Pipeline & FSM tab body so a reviewer landing
 * on a doc sees the trust signal next to the FSM action card without
 * a route change. Empty + error states render inline; the panel
 * never blocks the surrounding cards.
 */

import { useEffect, useState } from "react";
import type { ReactElement } from "react";

import { Card, CardHead, SectionH } from "../index";
import { ApiError, getDocumentConfidence } from "../../api/client";
import type {
  ApiConfidenceScore,
  ApiDocumentConfidenceResponse,
} from "../../api/types";

export interface ConfidencePanelProps {
  /** The active document id (from `useParams`). When ``null`` the
   *  panel renders the "pick a document" empty state. */
  documentId: string | null;
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: ApiDocumentConfidenceResponse }
  | { kind: "error"; message: string };

export function ConfidencePanel({
  documentId,
}: ConfidencePanelProps): ReactElement {
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
    getDocumentConfidence(documentId, { signal: controller.signal })
      .then((data) => setState({ kind: "ready", data }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) {
          setState({ kind: "error", message: err.detail });
        } else if (err instanceof Error) {
          setState({ kind: "error", message: err.message });
        } else {
          setState({ kind: "error", message: "Failed to load confidence." });
        }
      });
    return () => controller.abort();
  }, [documentId]);

  if (state.kind === "idle") {
    return (
      <Card>
        <CardHead>
          <SectionH>Confidence</SectionH>
        </CardHead>
        <div className="kf-confidence__empty">
          Pick a document from the rail to see its confidence breakdown.
        </div>
      </Card>
    );
  }

  if (state.kind === "loading") {
    return (
      <Card>
        <CardHead>
          <SectionH>Confidence</SectionH>
        </CardHead>
        <div
          className="kf-confidence__empty"
          role="status"
          data-testid="kf-confidence-loading"
        >
          Loading confidence…
        </div>
      </Card>
    );
  }

  if (state.kind === "error") {
    return (
      <Card>
        <CardHead>
          <SectionH>Confidence</SectionH>
        </CardHead>
        <div
          className="notice danger"
          role="alert"
          data-testid="kf-confidence-error"
        >
          <strong>Failed to load confidence.</strong>
          <span>{state.message}</span>
        </div>
      </Card>
    );
  }

  return <ConfidenceReady data={state.data} />;
}

interface ConfidenceReadyProps {
  data: ApiDocumentConfidenceResponse;
}

function ConfidenceReady({ data }: ConfidenceReadyProps): ReactElement {
  const score = data.confidence_score ?? null;
  const hasScore = Boolean(data.has_score && score !== null);
  return (
    <Card>
      <CardHead
        right={
          <span className="orb-mono kf-card-hint">
            v{data.version_number}
          </span>
        }
      >
        <SectionH>Confidence</SectionH>
      </CardHead>
      <div className="kf-confidence" data-testid="kf-confidence">
        <Overall
          score={score}
          threshold={data.auto_validate_threshold}
          hasScore={hasScore}
        />
        <RoutingRow
          routing={data.routing_decision ?? null}
          method={data.validation_method ?? null}
          actor={data.validation_actor ?? null}
        />
        {hasScore && score !== null ? <Signals score={score} /> : null}
      </div>
    </Card>
  );
}

interface OverallProps {
  score: ApiConfidenceScore | null;
  threshold: number;
  hasScore: boolean;
}

function Overall({ score, threshold, hasScore }: OverallProps): ReactElement {
  if (!hasScore || score === null) {
    return (
      <div className="kf-confidence__overall" data-testid="kf-confidence-empty">
        <div className="kf-confidence__num orb-mono">—</div>
        <div className="kf-confidence__cap">
          No confidence data — this version predates the scorer or the
          scorer is disabled on this deployment.
        </div>
      </div>
    );
  }
  const passing = score.overall >= threshold;
  return (
    <div className="kf-confidence__overall">
      <div
        className={
          passing
            ? "kf-confidence__num orb-mono is-passing"
            : "kf-confidence__num orb-mono is-failing"
        }
        data-testid="kf-confidence-overall"
      >
        {_pct(score.overall)}
      </div>
      <div className="kf-confidence__cap">
        threshold {_pct(threshold)} · {passing ? "above" : "below"} auto-validate
        cut-off
      </div>
      <ThresholdBar overall={score.overall} threshold={threshold} />
    </div>
  );
}

interface ThresholdBarProps {
  overall: number;
  threshold: number;
}

function ThresholdBar({
  overall,
  threshold,
}: ThresholdBarProps): ReactElement {
  const clamped = Math.max(0, Math.min(1, overall));
  return (
    <div
      className="kf-confidence__bar"
      role="img"
      aria-label={`Confidence ${_pct(overall)} versus threshold ${_pct(threshold)}`}
    >
      <div
        className="kf-confidence__bar-fill"
        style={{ width: `${clamped * 100}%` }}
      />
      <div
        className="kf-confidence__bar-tick"
        style={{ left: `${Math.max(0, Math.min(1, threshold)) * 100}%` }}
        aria-hidden="true"
      />
    </div>
  );
}

interface RoutingRowProps {
  routing: ApiDocumentConfidenceResponse["routing_decision"];
  method: ApiDocumentConfidenceResponse["validation_method"];
  actor: string | null;
}

function RoutingRow({
  routing,
  method,
  actor,
}: RoutingRowProps): ReactElement {
  if (routing === null && method === null) {
    return (
      <div className="kf-confidence__routing" data-testid="kf-confidence-routing">
        <span className="kf-confidence__chip">routing · pending</span>
        <span className="muted">
          No HITL routing decision recorded for this version yet.
        </span>
      </div>
    );
  }
  return (
    <div className="kf-confidence__routing" data-testid="kf-confidence-routing">
      {routing !== null && (
        <span
          className={`kf-confidence__chip is-routing-${routing}`}
          data-testid="kf-confidence-routing-chip"
        >
          routed · {routing}
        </span>
      )}
      {method !== null && (
        <span
          className={`kf-confidence__chip is-method-${method}`}
          data-testid="kf-confidence-method-chip"
        >
          validated · {method}
        </span>
      )}
      {actor ? <span className="muted orb-mono">by {actor}</span> : null}
    </div>
  );
}

interface SignalsProps {
  score: ApiConfidenceScore;
}

function Signals({ score }: SignalsProps): ReactElement {
  const rows = Object.keys(score.signals)
    .sort()
    .map((name) => {
      const value = score.signals[name];
      const weight = score.weights[name];
      return { name, value, weight };
    });
  return (
    <ul
      className="kf-confidence__signals"
      data-testid="kf-confidence-signals"
      aria-label="Confidence signal breakdown"
    >
      {rows.map((row) => (
        <li key={row.name} className="kf-confidence__signal">
          <div className="kf-confidence__signal-h">
            <span className="kf-confidence__signal-name">
              {_humanizeSignal(row.name)}
            </span>
            <span className="orb-mono kf-confidence__signal-val">
              {_pct(row.value)}
              {row.weight !== undefined ? (
                <span className="muted"> · w {row.weight.toFixed(2)}</span>
              ) : null}
            </span>
          </div>
          <div className="kf-confidence__signal-bar" aria-hidden="true">
            <div
              className="kf-confidence__signal-bar-fill"
              style={{ width: `${Math.max(0, Math.min(1, row.value)) * 100}%` }}
            />
          </div>
        </li>
      ))}
      {score.ocr_override_active && (
        <li
          className="kf-confidence__signal is-flag"
          data-testid="kf-confidence-ocr-flag"
        >
          <div className="kf-confidence__signal-h">
            <span className="kf-confidence__signal-name">OCR override</span>
            <span className="orb-mono">active</span>
          </div>
        </li>
      )}
    </ul>
  );
}

function _pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function _humanizeSignal(raw: string): string {
  return raw
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
