/**
 * MetaRow — fixed-width mono key + flex-grow value, dashed underline.
 *
 * Stack these inside a Card to render the "Document detail" panel in
 * the Review tab. The key column is 110px, uppercase, mono — picked so
 * a 12-character key fits without wrapping at compact density.
 */
import type { ReactElement, ReactNode } from "react";

export interface MetaRowProps {
  /** The key. Rendered uppercase mono in a fixed 110px column. */
  k: ReactNode;
  /** The value. Wraps; `word-break: break-all` keeps long IDs readable. */
  children: ReactNode;
}

export function MetaRow({ k, children }: MetaRowProps): ReactElement {
  return (
    <div className="orb-meta-row">
      <span className="k">{k}</span>
      <span className="v">{children}</span>
    </div>
  );
}
