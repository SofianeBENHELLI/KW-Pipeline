/**
 * Renders a list capped at ``initialCount`` items, with an inline
 * "+N more" affordance that expands the list on click (#321).
 *
 * Originally inlined in ``DetailPanel.tsx``; promoted to its own
 * file when the local-fallback search dropdown in ``App.tsx``
 * needed the same affordance for its ``slice(0, 4)``-per-section
 * cap. Keeping it as a tiny standalone component (no package, no
 * Context) so callers can drop it next to existing ``.map()``
 * calls without rewiring their surrounding state.
 *
 * Lists at-or-below ``initialCount`` render exactly as before —
 * no button, no extra DOM nodes — so existing snapshots that
 * assert "no extra affordances on small fixtures" continue to
 * pass.
 *
 * Expansion is one-way (no "show less" toggle) — matches the
 * "show more" UX users already see in the Catalog component, and
 * avoids the surprise of clicking a row whose row-index moved
 * because the list collapsed under the user.
 */

import React, { type ReactNode } from "react";

export interface TruncatedListProps<T> {
  items: T[];
  initialCount: number;
  renderItem: (item: T, index: number) => ReactNode;
  /**
   * data-testid prefix for the "+N more" button so tests can
   * target a specific instance when several truncated lists share
   * one screen. The button gets ``${testIdPrefix}-more``; omit to
   * fall back to ``kx-truncated-more``.
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
}: TruncatedListProps<T>): React.ReactElement {
  const [expanded, setExpanded] = React.useState(false);
  const total = items.length;
  const visible = expanded || total <= initialCount ? items : items.slice(0, initialCount);
  const hidden = total - visible.length;
  return (
    <>
      {visible.map((item, index) => (
        <React.Fragment key={index}>{renderItem(item, index)}</React.Fragment>
      ))}
      {hidden > 0 && (
        <button
          type="button"
          className="kx-truncated-more"
          onClick={() => setExpanded(true)}
          data-testid={testIdPrefix ? `${testIdPrefix}-more` : "kx-truncated-more"}
          aria-label={`Show ${hidden} ${noun}`}
        >
          +{hidden} {noun}
        </button>
      )}
    </>
  );
}
