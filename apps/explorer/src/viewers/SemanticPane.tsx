/**
 * Renders the synthesised semantic document — the "structured /
 * synthesised" pane in the three-pane document layout.
 *
 * Lays out the document profile (type, purpose, audience, executive
 * summary), each semantic section, and the typed assets grouped by
 * `type`. Assets carry confidence scores and a `review_status` chip so
 * the reader sees at a glance which claims are validated vs awaiting
 * review.
 */

import React from "react";

import type {
  ReviewStatus,
  SemanticAsset,
  SemanticDocument,
} from "../api/types";

interface Props {
  semantic: SemanticDocument | null;
  loading: boolean;
  error: string | null;
  /** Highlight content tied to this source-reference id, used for cross-pane sync. */
  activeSourceReferenceId?: string | null;
  /** Notify the parent when the user picks a section/asset's source ref. */
  onPickSourceReference?: (sourceReferenceId: string) => void;
}

export const SemanticPane: React.FC<Props> = ({
  semantic,
  loading,
  error,
  activeSourceReferenceId = null,
  onPickSourceReference,
}) => {
  if (loading) return <p className="kw-status">Loading semantic synthesis…</p>;
  if (error !== null) {
    return (
      <p className="kw-error" role="alert">
        Failed to load semantic synthesis — {error}
      </p>
    );
  }
  if (semantic === null) {
    return (
      <p className="kw-status">
        Semantic synthesis has not been generated for this version yet.
      </p>
    );
  }

  const profile = semantic.document_profile;
  const assetsByType = groupAssetsByType(semantic.assets);

  return (
    <div className="kx-sem">
      <section className="kx-sem__profile">
        <h4 className="kx-sem__title">{profile.title || "(untitled)"}</h4>
        <dl className="kx-sem__profile-list">
          <ProfileRow label="Type" value={profile.document_type} />
          {profile.purpose && <ProfileRow label="Purpose" value={profile.purpose} />}
          {profile.audience && <ProfileRow label="Audience" value={profile.audience} />}
        </dl>
        {profile.executive_summary && (
          <p className="kx-sem__summary">{profile.executive_summary}</p>
        )}
      </section>

      {semantic.warnings.length > 0 && (
        <section className="kx-sem__warnings" aria-label="Semantic warnings">
          {semantic.warnings.map((w, i) => (
            <div key={i} className="kw-error">
              {w}
            </div>
          ))}
        </section>
      )}

      <section className="kx-sem__sections" aria-label="Semantic sections">
        <h5 className="kx-sem__group-title">Sections ({semantic.sections.length})</h5>
        {semantic.sections.length === 0 ? (
          <p className="kw-status">No semantic sections were produced.</p>
        ) : (
          <ol className="kx-sem__section-list">
            {semantic.sections.map((s) => {
              const refId = s.source_reference_ids[0] ?? null;
              const isActive =
                activeSourceReferenceId !== null &&
                s.source_reference_ids.includes(activeSourceReferenceId);
              return (
                <li
                  key={s.id}
                  className={`kx-sem__section${isActive ? " kx-sem__section--active" : ""}`}
                >
                  <header className="kx-sem__section-hdr">
                    <span className="kx-sem__section-heading">{s.heading}</span>
                    {refId !== null && onPickSourceReference !== undefined && (
                      <button
                        type="button"
                        className="kw-btn kw-btn--sm kw-btn--ghost"
                        onClick={() => onPickSourceReference(refId)}
                      >
                        Show source
                      </button>
                    )}
                  </header>
                  <p className="kx-sem__section-body">{s.text}</p>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      <section className="kx-sem__assets" aria-label="Semantic assets">
        <h5 className="kx-sem__group-title">Assets ({semantic.assets.length})</h5>
        {assetsByType.length === 0 ? (
          <p className="kw-status">No semantic assets were extracted.</p>
        ) : (
          assetsByType.map(([type, assets]) => (
            <div key={type} className="kx-sem__asset-group">
              <h6 className="kx-sem__asset-type">
                {type} <span className="kw-mono kw-mono--muted">({assets.length})</span>
              </h6>
              <ul className="kx-sem__asset-list">
                {assets.map((a) => {
                  const refId = a.source_reference_ids[0] ?? null;
                  const isActive =
                    activeSourceReferenceId !== null &&
                    a.source_reference_ids.includes(activeSourceReferenceId);
                  return (
                    <li
                      key={a.id}
                      className={`kx-sem__asset${isActive ? " kx-sem__asset--active" : ""}`}
                    >
                      <ReviewChip status={a.review_status} />
                      <span className="kx-sem__asset-text">{a.text}</span>
                      <span
                        className="kw-mono kw-mono--muted"
                        title={`Confidence ${a.confidence}`}
                      >
                        {Math.round(a.confidence * 100)}%
                      </span>
                      {refId !== null && onPickSourceReference !== undefined && (
                        <button
                          type="button"
                          className="kw-btn kw-btn--sm kw-btn--ghost"
                          onClick={() => onPickSourceReference(refId)}
                        >
                          Source
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </section>
    </div>
  );
};

const ProfileRow: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <>
    <dt>{label}</dt>
    <dd>{value}</dd>
  </>
);

const ReviewChip: React.FC<{ status: ReviewStatus }> = ({ status }) => {
  const klass =
    status === "validated"
      ? "kw-badge kw-badge--success"
      : status === "rejected"
        ? "kw-badge kw-badge--danger"
        : status === "source_backed"
          ? "kw-badge kw-badge--info"
          : "kw-badge kw-badge--warn";
  return <span className={klass}>{status.replace("_", " ")}</span>;
};

function groupAssetsByType(assets: SemanticAsset[]): Array<[string, SemanticAsset[]]> {
  const map = new Map<string, SemanticAsset[]>();
  for (const a of assets) {
    const existing = map.get(a.type);
    if (existing) existing.push(a);
    else map.set(a.type, [a]);
  }
  return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
}
