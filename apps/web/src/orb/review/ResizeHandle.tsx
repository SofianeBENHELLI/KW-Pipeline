/**
 * Thin vertical drag handle between two resizable columns.
 *
 * The visible affordance is a 1-px rule that matches the surrounding
 * pane borders, but the hit target is 6 px wide so the handle is
 * pointer-friendly without painting a heavy gutter into the layout.
 * Hover / drag states fade in the accent stroke so the user can see
 * the live drag target.
 *
 * Visually inert when ``disabled`` — used to hide the handle while
 * the rail is collapsed (drag would resize a hidden column).
 */

import type { PointerEventHandler, ReactElement } from "react";

export interface ResizeHandleProps {
  readonly onPointerDown: PointerEventHandler<HTMLElement>;
  readonly isDragging?: boolean;
  readonly disabled?: boolean;
  /** ARIA label for screen readers, e.g. "Resize document rail". */
  readonly label: string;
}

export function ResizeHandle({
  onPointerDown,
  isDragging = false,
  disabled = false,
  label,
}: ResizeHandleProps): ReactElement {
  const classes = [
    "kf-resize",
    isDragging ? "is-dragging" : "",
    disabled ? "is-disabled" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={disabled ? -1 : 0}
      className={classes}
      onPointerDown={disabled ? undefined : onPointerDown}
      data-testid="kf-resize-handle"
    />
  );
}
