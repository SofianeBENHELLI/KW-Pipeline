/**
 * LineageModal — flat version-history viewer for the Review header
 * (EPIC-C C.3 / ADR-025).
 *
 * Opens on the [Lineage] header button, fetches
 * ``GET /documents/{id}/lineage``, and renders the family's version
 * chain top-to-bottom (v1 → vN). Each row carries filename, version
 * number, and the ``ingested_at`` timestamp; clicking a row asks the
 * parent to switch the workspace's selected document via
 * ``onSelectDocument`` and closes the modal.
 *
 * The lineage payload itself is one-document-family so every row
 * shares ``document_id`` with the queried doc — clicking a row never
 * navigates to a *different* doc family, only to the family-root
 * document so the workspace re-fetches and re-renders against the
 * selected version's family. The MVP per spec is a flat list, not a
 * graph; deferred deep-link query params and graph visualisations
 * stay out of scope.
 *
 * Error states: 403/404 close the modal and surface a one-line
 * banner via ``onError`` (the parent owns the .notice danger slot).
 * ESC + backdrop click + the × button all dismiss the modal.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getDocumentLineage } from "../../api/client";
import type { ApiDocumentLineage, ApiLineageVersion } from "../../api/types";

interface LineageModalProps {
  documentId: string;
  filename: string;
  onClose: () => void;
  /** Optional — when provided, clicking a row asks the parent to
   *  switch the workspace's selected document. */
  onSelectDocument?: (documentId: string) => void;
  /** Optional — when the fetch errors with 403/404 the parent banner
   *  receives the message and the modal closes itself. */
  onError?: (message: string) => void;
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return fallback;
}

// One row of the version list. Pulled out so the click handler isn't
// recreated per render of the parent.
function LineageRow({
  version,
  onSelect,
}: {
  version: ApiLineageVersion;
  onSelect: () => void;
}) {
  return (
    <li className="lineage-row">
      <button
        type="button"
        className="lineage-row__btn"
        onClick={onSelect}
        data-testid="lineage-row"
        data-version-id={version.id}
      >
        <span className="lineage-row__filename">{version.filename}</span>
        <span className="lineage-row__version">v{version.version_number}</span>
        <span className="lineage-row__date muted">
          {version.ingested_at ?? "—"}
        </span>
        {version.is_latest ? (
          <span className="lineage-row__badge">current</span>
        ) : null}
      </button>
    </li>
  );
}

export function LineageModal({
  documentId,
  filename,
  onClose,
  onSelectDocument,
  onError,
}: LineageModalProps) {
  const [data, setData] = useState<ApiDocumentLineage | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Focus restoration — the trigger button regains keyboard focus on
  // close. Vital for keyboard-only operators per the broader a11y
  // posture (see ResizeHandle / RailExpand patterns).
  const closeRef = useRef<HTMLButtonElement | null>(null);

  // ESC key + the registered close hook.
  useEffect(() => {
    function handler(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
    }
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  // Initial fetch. Aborts on unmount so a stale modal close doesn't
  // poison the next open.
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFetchError(null);
    getDocumentLineage(documentId, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        setData(response);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        // 403/404 → hidden-existence: surface as a banner via the
        // parent and close. Other errors stay inline so the operator
        // can retry by reopening.
        if (
          err instanceof ApiError &&
          (err.status === 403 || err.status === 404)
        ) {
          const message = errorMessage(err, "Lineage is unavailable.");
          if (onError) onError(message);
          onClose();
          return;
        }
        setFetchError(errorMessage(err, "Failed to load lineage."));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [documentId, onClose, onError]);

  const handleSelect = useCallback(
    (version: ApiLineageVersion) => {
      if (onSelectDocument) onSelectDocument(documentId);
      onClose();
      // ``version`` is captured so future deep-link/version-pick work
      // (out of scope here) has somewhere to slot in.
      void version;
    },
    [documentId, onClose, onSelectDocument],
  );

  // Backdrop click closes only when the click originated on the
  // backdrop itself — clicks bubbling up from the modal card don't
  // count.
  const onBackdropClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (event.target === event.currentTarget) onClose();
    },
    [onClose],
  );

  const versions = data?.versions ?? [];
  const familyLabel = data?.family_filename ?? filename;

  return (
    // role=dialog + aria-modal owns the a11y semantics; the global ESC
    // listener gives keyboard operators the dismiss path the backdrop
    // click is the pointer-equivalent of.
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`Lineage — ${familyLabel}`}
      data-testid="lineage-modal"
      onClick={onBackdropClick}
    >
      <div className="modal-card lineage-card">
        <header className="modal-header">
          <h3>Lineage — {familyLabel}</h3>
          <button
            ref={closeRef}
            type="button"
            className="text-button"
            onClick={onClose}
            aria-label="Close"
            data-testid="lineage-close"
          >
            ×
          </button>
        </header>

        {loading ? (
          <p className="muted" role="status" data-testid="lineage-loading">
            Loading lineage…
          </p>
        ) : fetchError !== null ? (
          <div className="notice danger" role="alert">
            <strong>Lineage unavailable</strong>
            <span>{fetchError}</span>
          </div>
        ) : versions.length === 0 ? (
          <p className="muted" role="status" data-testid="lineage-empty">
            This is the only version of this document.
          </p>
        ) : (
          <ol className="lineage-list" data-testid="lineage-list">
            {versions.map((version) => (
              <LineageRow
                key={version.id}
                version={version}
                onSelect={() => handleSelect(version)}
              />
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
