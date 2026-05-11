/**
 * Reviewer-facing list of ``SemanticSection`` rows (#408).
 *
 * Each row renders a heading + collapsed text preview. Click toggles
 * full-text expansion. The source-reference IDs link the section
 * back to the raw extraction lineage (rendered as inline ``<code>``
 * tokens; the existing extraction view is the deep target).
 *
 * Long lists (default cap 12) get a ``+N more`` affordance via
 * :class:`TruncatedList` so a 51-section spec doc doesn't overwhelm
 * the right-rail panel.
 *
 * Empty state ("No sections extracted") covers the rare case of a
 * doc that parsed but produced no semantic sections (e.g. an empty
 * file or a parser-only-warnings run).
 */

import { useState, type ReactElement } from "react";
import type { ApiSemanticSection } from "../../api/types";
import { TruncatedList } from "./TruncatedList";

export interface SemanticSectionListProps {
  sections: ApiSemanticSection[];
  /** Optional cap before showing the +N more affordance. Default 12. */
  initialCount?: number;
}

const PREVIEW_CHARS = 220;

function previewText(text: string): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  if (collapsed.length <= PREVIEW_CHARS) return collapsed;
  return `${collapsed.slice(0, PREVIEW_CHARS).trimEnd()}…`;
}

export function SemanticSectionList({
  sections,
  initialCount = 12,
}: SemanticSectionListProps): ReactElement {
  if (sections.length === 0) {
    return (
      <p className="muted sem-empty" data-testid="sem-sections-empty">
        No sections extracted.
      </p>
    );
  }
  return (
    <ul className="sem-list sem-section-list" data-testid="sem-sections-list">
      <TruncatedList
        items={sections}
        initialCount={initialCount}
        testIdPrefix="sem-sections"
        renderItem={(section) => <SectionRow key={section.id} section={section} />}
      />
    </ul>
  );
}

function SectionRow({ section }: { section: ApiSemanticSection }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const preview = previewText(section.text);
  const overflows = section.text.replace(/\s+/g, " ").trim().length > PREVIEW_CHARS;
  return (
    <li
      className={`sem-row sem-section${expanded ? " sem-section--expanded" : ""}`}
      data-testid="sem-section-row"
    >
      <button
        type="button"
        className="sem-section__head"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        data-testid="sem-section-toggle"
      >
        <span className="sem-section__heading">{section.heading || "(untitled section)"}</span>
        <span className="sem-section__chevron" aria-hidden="true">
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      <p className="sem-section__text" data-testid="sem-section-text">
        {expanded || !overflows ? section.text : preview}
      </p>
      {section.source_reference_ids.length > 0 && (
        <p className="sem-section__refs" data-testid="sem-section-refs">
          <span className="sem-row__refs-label">Source refs:</span>{" "}
          {section.source_reference_ids.map((ref, i) => (
            <span key={ref}>
              {i > 0 && " · "}
              <code>{ref}</code>
            </span>
          ))}
        </p>
      )}
    </li>
  );
}
