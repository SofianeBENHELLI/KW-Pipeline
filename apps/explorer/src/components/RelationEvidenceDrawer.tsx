/**
 * Relation evidence drawer (#318 partial) — explains *why* the
 * projection drew an edge between two documents.
 *
 * Triggered from the DetailPanel "Related Documents" list. Calls
 * ``GET /knowledge/relations/aggregate`` once when a (source, target)
 * pair is set, renders the aggregated metrics + the top contributing
 * chunk pairs.
 *
 * Render branches mirror the search dropdown so behaviour stays
 * consistent across the explorer:
 *
 *   - ``"loading"`` → "Loading evidence…" affordance
 *   - ``"data"``    → metrics + contributing pairs
 *   - ``"empty"``   → "no shared evidence" (404 from backend means the
 *                     projection didn't materialise a cross-doc edge)
 *   - ``"error"``   → red banner with the message
 *
 * Closing semantics match LineageModal: ESC, backdrop click, or the
 * close button. Focus is restored on close.
 *
 * Scope-bounded for the partial — wires off DetailPanel only. The
 * GraphCanvas edge-click affordance and the single-edge inspector
 * (``getRelationEvidence``) are deferred to a follow-up that also
 * tackles #320 ranking controls.
 */

import React, { useCallback, useEffect, useRef } from "react";

import { ApiError } from "../api/client";
import type { ContributingChunkPair } from "../api/types";
import {
  type AggregateRelationEvidenceSnapshot,
  useAggregateRelationEvidence,
} from "../state/use-aggregate-relation-evidence";
import { Icon } from "./icons";

export interface RelationEvidenceDrawerProps {
  sourceDocumentId: string;
  sourceTitle: string;
  targetDocumentId: string;
  targetTitle: string;
  onClose: () => void;
  /** Optional override (mostly for tests). Defaults to 5. */
  topN?: number;
}

function formatScore(score: number | null): string {
  if (score === null) return "—";
  return `${(score * 100).toFixed(1)}%`;
}

function strengthLabel(strength: string | null): string {
  if (strength === null) return "—";
  return strength;
}

export const RelationEvidenceDrawer: React.FC<RelationEvidenceDrawerProps> = ({
  sourceDocumentId,
  sourceTitle,
  targetDocumentId,
  targetTitle,
  onClose,
  topN = 5,
}) => {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const previousActiveRef = useRef<Element | null>(null);

  const snapshot = useAggregateRelationEvidence(
    { sourceDocumentId, targetDocumentId },
    { topN },
  );

  // ESC / Tab focus trap mirrors LineageModal — the only differences
  // are the aria-label and the data-testid markers.
  useEffect(() => {
    previousActiveRef.current = window.document.activeElement;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "Tab") {
        const root = dialogRef.current;
        if (!root) return;
        const focusables = root.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = window.document.activeElement as HTMLElement | null;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      const prev = previousActiveRef.current;
      if (prev && prev instanceof HTMLElement) prev.focus();
    };
  }, [onClose]);

  const onBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  return (
    <div
      className="kx-evidence-backdrop"
      data-testid="kx-evidence-backdrop"
      onClick={onBackdropClick}
    >
      <div
        ref={dialogRef}
        className="kx-evidence-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Relation evidence"
        data-testid="kx-evidence-modal"
        data-state={snapshot.state}
      >
        <header className="kx-evidence-head">
          <div className="kx-evidence-head-t">
            <div className="kx-kind">RELATION EVIDENCE</div>
            <div className="kx-evidence-pair">
              <span className="kx-evidence-doc" title={sourceTitle}>
                {sourceTitle}
              </span>
              <span className="kx-evidence-arrow" aria-hidden="true">
                ↔
              </span>
              <span className="kx-evidence-doc" title={targetTitle}>
                {targetTitle}
              </span>
            </div>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="kx-icon-btn"
            onClick={onClose}
            aria-label="Close relation evidence"
            data-testid="kx-evidence-close"
          >
            <Icon name="x" size={12} />
          </button>
        </header>

        <div className="kx-evidence-body">
          <DrawerBody snapshot={snapshot} />
        </div>

        <footer className="kx-evidence-foot">
          <button
            type="button"
            className="kx-btn"
            onClick={onClose}
            data-testid="kx-evidence-close-foot"
          >
            Close
          </button>
        </footer>
      </div>
    </div>
  );
};

interface DrawerBodyProps {
  snapshot: AggregateRelationEvidenceSnapshot;
}

const DrawerBody: React.FC<DrawerBodyProps> = ({ snapshot }) => {
  if (snapshot.state === "loading" || snapshot.state === "idle") {
    return (
      <div className="kx-evidence-empty" data-testid="kx-evidence-loading">
        Loading evidence…
      </div>
    );
  }

  if (snapshot.state === "error") {
    const message =
      snapshot.error instanceof ApiError
        ? snapshot.error.message
        : typeof snapshot.error === "string"
          ? snapshot.error
          : "Failed to load evidence.";
    return (
      <div
        className="kx-evidence-empty kx-evidence-error"
        role="alert"
        data-testid="kx-evidence-error"
      >
        {message}
      </div>
    );
  }

  if (snapshot.state === "empty" || snapshot.evidence === null) {
    return (
      <div className="kx-evidence-empty" data-testid="kx-evidence-empty">
        No shared evidence — these documents are not directly linked in
        the projection.
      </div>
    );
  }

  const { evidence } = snapshot;
  return (
    <>
      <div className="kx-evidence-metrics" data-testid="kx-evidence-metrics">
        <div className="kx-evidence-metric">
          <div className="kx-evidence-metric-l">AGGREGATE SCORE</div>
          <div className="kx-evidence-metric-v kx-mono">
            {formatScore(evidence.aggregate_score)}
          </div>
        </div>
        <div className="kx-evidence-metric">
          <div className="kx-evidence-metric-l">PAIRS</div>
          <div className="kx-evidence-metric-v kx-mono">{evidence.pair_count}</div>
        </div>
        {evidence.is_bridge && (
          <span
            className="kx-cat-badge kx-stat-info kx-evidence-flag"
            data-testid="kx-evidence-bridge"
          >
            bridge
          </span>
        )}
        {evidence.is_outlier && (
          <span
            className="kx-cat-badge kx-stat-warn kx-evidence-flag"
            data-testid="kx-evidence-outlier"
          >
            outlier
          </span>
        )}
      </div>

      <div className="kx-evidence-sec">
        <div className="kx-search-h">
          TOP CONTRIBUTING PAIRS · {evidence.top_contributing_pairs.length}
        </div>
        {evidence.top_contributing_pairs.length === 0 ? (
          <div
            className="kx-evidence-empty"
            data-testid="kx-evidence-no-pairs"
          >
            Edge exists but no contributing pair detail is available.
          </div>
        ) : (
          <ul className="kx-evidence-list" data-testid="kx-evidence-pairs">
            {evidence.top_contributing_pairs.map((pair, idx) => (
              <ContributingPairRow key={`${pair.relation_id}-${idx}`} pair={pair} />
            ))}
          </ul>
        )}
      </div>
    </>
  );
};

interface ContributingPairRowProps {
  pair: ContributingChunkPair;
}

const ContributingPairRow: React.FC<ContributingPairRowProps> = ({ pair }) => (
  <li className="kx-evidence-row" data-testid="kx-evidence-pair">
    <div className="kx-evidence-row-head">
      <span className="kx-evidence-row-score kx-mono">
        {formatScore(pair.score)}
      </span>
      <span className="kx-cat-badge kx-stat-info">{pair.kind}</span>
      <span className="kx-cat-badge kx-stat-muted">
        {strengthLabel(pair.strength_class)}
      </span>
    </div>
    <div className="kx-evidence-row-chunks kx-mono kx-mute kx-sm">
      <span title={pair.source_chunk_id}>{pair.source_chunk_id}</span>
      <span aria-hidden="true"> ↔ </span>
      <span title={pair.target_chunk_id}>{pair.target_chunk_id}</span>
    </div>
    {pair.reason && (
      <div className="kx-evidence-row-reason">{pair.reason}</div>
    )}
    {pair.shared_keywords.length > 0 && (
      <div className="kx-evidence-row-keywords">
        {pair.shared_keywords.slice(0, 6).map((kw) => (
          <span key={kw} className="kx-tag kx-tag-static">
            {kw}
          </span>
        ))}
      </div>
    )}
  </li>
);

export default RelationEvidenceDrawer;
