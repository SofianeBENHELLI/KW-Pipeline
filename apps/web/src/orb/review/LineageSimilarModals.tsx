/**
 * LineageModal + SimilarDocumentsModal — review header companions.
 *
 * Both modals reuse the admin ``ModalShell`` primitive for the
 * backdrop + header chrome so the review surface stays visually
 * consistent with the admin surfaces. They fetch their respective
 * EPIC-C C.3 routes on mount, handle the empty / loading / error
 * states inline, and let the operator click through to a sibling
 * document.
 */

import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import { ModalShell } from "../../features/admin/ModalShell";
import {
  ApiError,
  getDocumentLineage,
  getSimilarDocuments,
} from "../../api/client";
import type {
  ApiLineageResponse,
  ApiSimilarDocumentsResponse,
} from "../../api/types";

// ─── Lineage modal ─────────────────────────────────────────────────────────

interface LineageModalProps {
  documentId: string;
  onClose: () => void;
}

export function LineageModal({
  documentId,
  onClose,
}: LineageModalProps): ReactElement {
  const [data, setData] = useState<ApiLineageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getDocumentLineage(documentId, { signal: controller.signal })
      .then((response) => setData(response))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) setError(err.detail);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load lineage.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [documentId]);

  return (
    <ModalShell title="Version lineage" onClose={onClose}>
      <div className="modal-body" data-testid="kf-lineage-modal">
        {loading && (
          <p className="muted" role="status">
            Loading lineage…
          </p>
        )}
        {error && (
          <div className="notice danger" role="alert">
            <strong>Failed to load lineage.</strong>
            <span>{error}</span>
          </div>
        )}
        {data && data.versions.length === 0 && (
          <p
            className="muted"
            data-testid="kf-lineage-empty"
          >
            No lineage history yet. New versions will appear here as they
            are uploaded.
          </p>
        )}
        {data && data.versions.length > 0 && (
          <>
            <p className="muted">
              <strong>{data.family_filename}</strong> — every version of
              this document family, oldest to newest.
            </p>
            <ol
              className="kf-lineage-list"
              data-testid="kf-lineage-list"
            >
              {data.versions.map((v) => (
                <li
                  key={v.id}
                  className="kf-lineage-row"
                  data-testid={`kf-lineage-row-${v.version_number}`}
                >
                  <div className="kf-lineage-row__meta">
                    <span className="orb-mono">v{v.version_number}</span>
                    <span className="muted">
                      {v.filename} · {v.status}
                      {v.is_latest && (
                        <span className="kf-lineage-row__pill"> latest</span>
                      )}
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </ModalShell>
  );
}

// ─── Similar documents modal ──────────────────────────────────────────────

interface SimilarDocumentsModalProps {
  documentId: string;
  onClose: () => void;
}

export function SimilarDocumentsModal({
  documentId,
  onClose,
}: SimilarDocumentsModalProps): ReactElement {
  const navigate = useNavigate();
  const [data, setData] = useState<ApiSimilarDocumentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getSimilarDocuments(documentId, { signal: controller.signal })
      .then((response) => setData(response))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError) setError(err.detail);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load similar documents.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [documentId]);

  return (
    <ModalShell title="Similar documents" onClose={onClose}>
      <div className="modal-body" data-testid="kf-similar-modal">
        {loading && (
          <p className="muted" role="status">
            Loading neighbours…
          </p>
        )}
        {error && (
          <div className="notice danger" role="alert">
            <strong>Failed to load similar documents.</strong>
            <span>{error}</span>
          </div>
        )}
        {data && data.results.length === 0 && (
          <p
            className="muted"
            data-testid="kf-similar-empty"
          >
            No similar documents yet. Once this document is projected
            into the knowledge layer, neighbours by topic-Jaccard will
            surface here.
          </p>
        )}
        {data && data.results.length > 0 && (
          <ol
            className="kf-similar-list"
            data-testid="kf-similar-list"
          >
            {data.results.map((s) => (
              <li
                key={s.document_id}
                className="kf-similar-row"
                data-testid={`kf-similar-row-${s.document_id}`}
              >
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    onClose();
                    navigate(`/kf/review/${s.document_id}`);
                  }}
                >
                  <span>{s.family_filename}</span>
                  <span className="orb-mono muted">
                    {" · "}
                    {(s.similarity * 100).toFixed(0)}% match · {s.latest_version_status}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </div>
    </ModalShell>
  );
}
