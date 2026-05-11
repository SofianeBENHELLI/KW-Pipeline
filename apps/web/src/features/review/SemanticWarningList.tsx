/**
 * Reviewer-facing list of extractor warnings (#408).
 *
 * Each warning is a flat string emitted by the SemanticExtractor —
 * usually a low-confidence section flag, a parse anomaly, or a
 * missing source-reference notice. Render one per row so a reviewer
 * can scan them before validating.
 *
 * Empty state ("No warnings — extractor ran cleanly.") is the
 * common case for a healthy run; we still render the panel so the
 * reviewer always sees the explicit "no warnings" signal rather
 * than wondering whether the section is missing.
 */

import type { ReactElement } from "react";
import { TruncatedList } from "./TruncatedList";

export interface SemanticWarningListProps {
  warnings: string[];
  /** Optional cap before showing the +N more affordance. Default 12. */
  initialCount?: number;
}

export function SemanticWarningList({
  warnings,
  initialCount = 12,
}: SemanticWarningListProps): ReactElement {
  if (warnings.length === 0) {
    return (
      <p className="muted sem-empty" data-testid="sem-warnings-empty">
        No warnings — extractor ran cleanly.
      </p>
    );
  }
  return (
    <ul className="sem-list sem-warning-list" data-testid="sem-warnings-list">
      <TruncatedList
        items={warnings}
        initialCount={initialCount}
        testIdPrefix="sem-warnings"
        renderItem={(warning, i) => (
          <li className="sem-row sem-warning" key={i} data-testid="sem-warning-row">
            <span className="sem-warning__glyph" aria-hidden="true">
              ⚠
            </span>
            <span className="sem-warning__text">{warning}</span>
          </li>
        )}
      />
    </ul>
  );
}
