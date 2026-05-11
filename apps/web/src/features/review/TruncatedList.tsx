/**
 * Renders a list capped at ``initialCount`` items, with an inline
 * "+N more" affordance that expands the list on click (#408).
 *
 * Mirrors the Explorer-side ``TruncatedList`` (from PR #398) but
 * inlined here to keep Orbital self-contained for #408. If a third
 * frontend ever needs the same affordance, promote both copies into
 * ``apps/_shared`` rather than continuing to copy the pattern.
 *
 * Expansion is one-way (no "show less" toggle) — matches the "+N
 * more" UX users already see in the Explorer detail panels.
 */

import { Fragment, useState, type ReactElement, type ReactNode } from "react";

export interface TruncatedListProps<T> {
  items: T[];
  initialCount: number;
  renderItem: (item: T, index: number) => ReactNode;
  /**
   * Optional data-testid prefix for the "+N more" button so tests can
   * target a specific instance when several truncated lists share one
   * screen. Falls back to ``"sem-truncated-more"`` when omitted.
   */
  testIdPrefix?: string;
  /** Word used in the affordance label. Defaults to ``"more"``. */
  noun?: string;
}

export function TruncatedList<T>({
  items,
  initialCount,
  renderItem,
  testIdPrefix,
  noun = "more",
}: TruncatedListProps<T>): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const total = items.length;
  const visible = expanded || total <= initialCount ? items : items.slice(0, initialCount);
  const hidden = total - visible.length;
  return (
    <>
      {visible.map((item, index) => (
        <Fragment key={index}>{renderItem(item, index)}</Fragment>
      ))}
      {hidden > 0 && (
        <button
          type="button"
          className="sem-truncated-more"
          onClick={() => setExpanded(true)}
          data-testid={testIdPrefix ? `${testIdPrefix}-more` : "sem-truncated-more"}
          aria-label={`Show ${hidden} ${noun}`}
        >
          +{hidden} {noun}
        </button>
      )}
    </>
  );
}
