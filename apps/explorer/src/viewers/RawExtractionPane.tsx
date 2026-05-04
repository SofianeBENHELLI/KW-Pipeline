/**
 * Renders the raw parser output — the "what was captured" pane in the
 * three-pane document layout.
 *
 * The pane prefers the structured per-section view (heading + body
 * snippet + page number when known) and falls back to the raw `text`
 * blob when the parser did not split the document into sections (e.g.
 * the plain-text parser).
 */

import React from "react";

import { Icon } from "../components/icons";
import type { RawExtraction } from "../api/types";

interface Props {
  extraction: RawExtraction | null;
  loading: boolean;
  error: string | null;
  /** Highlight a section by source-reference id, used for cross-pane sync. */
  activeSourceReferenceId?: string | null;
  /** Notify the parent which source ref the user clicked. */
  onPickSourceReference?: (sourceReferenceId: string) => void;
}

export const RawExtractionPane: React.FC<Props> = ({
  extraction,
  loading,
  error,
  activeSourceReferenceId = null,
  onPickSourceReference,
}) => {
  if (loading) return <p className="kw-status">Loading extraction…</p>;
  if (error !== null) {
    return (
      <p className="kw-error" role="alert">
        Failed to load raw extraction — {error}
      </p>
    );
  }
  if (extraction === null) {
    return (
      <p className="kw-status">
        Raw extraction has not been generated for this version yet.
      </p>
    );
  }

  const sections = extraction.sections;
  if (sections.length === 0) {
    return (
      <pre className="kx-pane__pre">
        {extraction.text || "(parser produced no text)"}
      </pre>
    );
  }

  return (
    <div className="kx-raw">
      <div className="kx-raw__meta">
        <span className="kw-mono kw-mono--muted">
          {extraction.parser_name}@{extraction.parser_version}
        </span>
        <span className="kw-mono kw-mono--muted">{sections.length} sections</span>
      </div>
      <ol className="kx-raw__sections">
        {sections.map((section, index) => {
          const refId = section.source_reference_ids[0] ?? null;
          const isActive =
            activeSourceReferenceId !== null &&
            refId !== null &&
            section.source_reference_ids.includes(activeSourceReferenceId);
          return (
            <li
              key={section.id}
              className={`kx-raw__section${isActive ? " kx-raw__section--active" : ""}`}
            >
              <header className="kx-raw__section-hdr">
                <span className="kx-raw__section-num">#{index + 1}</span>
                <span className="kx-raw__section-heading">{section.heading}</span>
                {section.page_number !== null && (
                  <span className="kw-mono kw-mono--muted">p. {section.page_number}</span>
                )}
                {refId !== null && onPickSourceReference !== undefined && (
                  <button
                    type="button"
                    className="kw-iconbtn"
                    title="Cross-link this passage with the semantic pane"
                    aria-label="Cross-link"
                    onClick={() => onPickSourceReference(refId)}
                  >
                    <Icon name="info" size={12} />
                  </button>
                )}
              </header>
              <pre className="kx-pane__pre kx-pane__pre--inset">{section.text}</pre>
            </li>
          );
        })}
      </ol>
    </div>
  );
};
