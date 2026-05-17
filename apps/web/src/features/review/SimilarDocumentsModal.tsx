/**
 * SimilarDocumentsModal — top-K topic-Jaccard neighbours surface
 * (EPIC-C C.3 / ADR-025 §3).
 *
 * Opens on the [Similar documents] header button, fetches
 * ``GET /documents/{id}/similar``, and renders the ranked results as
 * a flat table. Confidence is formatted as ``%`` because the backend
 * returns a similarity ratio in ``[0.0, 1.0]`` and a percentage is the
 * shape reviewers can scan; the precise float is still in the DOM via
 * the title attribute for power users / tests.
 *
 * Empty results (cold-start: no topics on the query doc) collapse to
 * the spec'd "No similar documents found above the confidence
 * threshold." hint. 403/404 errors hand the message to the parent's
 * banner and close the modal — matching the LineageModal pattern.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getSimilarDocuments } from "../../api/client";
import type { ApiSimilarDocument, ApiSimilarDocuments } from "../../api/types";

interface SimilarDocumentsModalProps {
  documentId: string;
  filename: string;
  onClose: () => void;
  /** Optional — clicking [Open] asks the parent to switch the
   *  workspace's selected document to the neighbour. */
  onSelectDocument?: (documentId: string) => void;
  /** Optional — surfaces 403/404 fetch failures to the parent banner. */
  onError?: (message: string) => void;
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return fallback;
}

/** Backend returns a Jaccard ratio in ``[0.0, 1.0]``; render it as
 *  a whole percentage. Rounds rather than truncates so 0.999 doesn't
 *  display as 99% next to a 1.000 that displays as 100%. */
export function formatConfidence(similarity: number): string {
  const clamped = Math.max(0, Math.min(1, similarity));
  return `${Math.round(clamped * 100)}%`;
}

interface SimilarRowProps {
  row: ApiSimilarDocument;
  onOpen: () => void;
}

function SimilarRow({ row, onOpen }: SimilarRowProps) {
  return (
    <tr data-testid="similar-row" data-document-id={row.document_id}>
      <td className="similar-cell similar-cell--filename">
        {row.family_filename}
      </td>
      <td
        className="similar-cell similar-cell--confidence"
        data-testid="similar-confidence"
        title={row.similarity.toFixed(4)}
      >
        {formatConfidence(row.similarity)}
      </td>
      <td className="similar-cell similar-cell--open">
        <button
          type="button"
          className="text-button"
          onClick={onOpen}
          data-testid="similar-open"
          data-document-id={row.document_id}
        >
          Open
        </button>
      </td>
    </tr>
  );
}

export function SimilarDocumentsModal({
  documentId,
  filename,
  onClose,
  onSelectDocument,
  onError,
}: SimilarDocumentsModalProps) {
  const [data, setData] = useState<ApiSimilarDocuments | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    function handler(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
    }
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFetchError(null);
    getSimilarDocuments(documentId, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        setData(response);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (
          err instanceof ApiError &&
          (err.status === 403 || err.status === 404)
        ) {
          const message = errorMessage(
            err,
            "Similar documents are unavailable.",
          );
          if (onError) onError(message);
          onClose();
          return;
        }
        setFetchError(errorMessage(err, "Failed to load similar documents."));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [documentId, onClose, onError]);

  const handleOpen = useCallback(
    (neighbourId: string) => {
      if (onSelectDocument) onSelectDocument(neighbourId);
      onClose();
    },
    [onClose, onSelectDocument],
  );

  const onBackdropClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget) onClose();
    },
    [onClose],
  );

  const results = data?.results ?? [];

  return (
    // role=dialog + aria-modal owns the a11y semantics; the global ESC
    // listener gives keyboard operators the dismiss path the backdrop
    // click is the pointer-equivalent of.
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`Similar documents — ${filename}`}
      data-testid="similar-modal"
      onClick={onBackdropClick}
    >
      <div className="modal-card similar-card">
        <header className="modal-header">
          <h3>Similar documents — {filename}</h3>
          <button
            ref={closeRef}
            type="button"
            className="text-button"
            onClick={onClose}
            aria-label="Close"
            data-testid="similar-close"
          >
            ×
          </button>
        </header>

        {loading ? (
          <p className="muted" role="status" data-testid="similar-loading">
            Loading similar documents…
          </p>
        ) : fetchError !== null ? (
          <div className="notice danger" role="alert">
            <strong>Similar documents unavailable</strong>
            <span>{fetchError}</span>
          </div>
        ) : results.length === 0 ? (
          <p className="muted" role="status" data-testid="similar-empty">
            No similar documents found above the confidence threshold.
          </p>
        ) : (
          <table className="similar-table" data-testid="similar-table">
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Confidence</th>
                <th scope="col" aria-label="Open" />
              </tr>
            </thead>
            <tbody>
              {results.map((row) => (
                <SimilarRow
                  key={row.document_id}
                  row={row}
                  onOpen={() => handleOpen(row.document_id)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
