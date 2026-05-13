/**
 * Generic horizontal-resize state machine for split-pane layouts.
 *
 * The Knowledge Forge ``/kf/review`` surface has two split points:
 *
 *   1. Doc rail ↔ main pane (outer split, with a collapse affordance)
 *   2. Document viewer ↔ knowledge objects (inner LinkedView split)
 *
 * Both reuse this hook so the drag semantics, clamping, and
 * ``localStorage`` persistence behave identically.
 *
 * Design notes:
 *
 * - **Pointer events, not mouse**: pointer events work across mouse,
 *   touch, and pen without three duplicated branches. ``setPointerCapture``
 *   guarantees we keep getting move events even when the cursor leaves
 *   the handle (common when dragging fast).
 * - **Persistence is opt-in via ``storageKey``**: the handle stores its
 *   own value (number for px, or a tuple); tests can omit the key to
 *   avoid bleed between cases.
 * - **Clamping**: ``min`` / ``max`` are required and enforced on every
 *   update so a saved value from an older release that's now out of
 *   range never strands the column off-screen.
 * - **Body-side cursor + select hijack**: while dragging we slap a
 *   `col-resize` cursor on ``document.body`` and disable text selection
 *   so the rest of the page doesn't render mid-drag artefacts. Both
 *   are cleaned up on pointer-up / abort.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface UseResizableOptions {
  /** Initial value (before any saved override is applied). */
  readonly initial: number;
  /** Minimum allowed value. Saved overrides below this are clamped up. */
  readonly min: number;
  /** Maximum allowed value. Saved overrides above this are clamped down. */
  readonly max: number;
  /**
   * Optional ``localStorage`` key. When set, the hook hydrates from it
   * on first render and writes back every time the user drags. Tests
   * (and call sites that don't want persistence) leave it ``undefined``.
   */
  readonly storageKey?: string;
  /**
   * Reverses the drag delta sign — set to ``"right-to-left"`` for a
   * handle that lives on the *right* edge of the resizable column
   * (e.g. the rail handle: dragging right grows the rail, but the
   * client X delta is positive in both cases so the call site picks
   * the orientation that matches its layout intent).
   */
  readonly direction?: "left-to-right" | "right-to-left";
}

export interface UseResizableResult {
  readonly value: number;
  readonly setValue: (next: number) => void;
  /**
   * Attach to the resize handle's ``onPointerDown`` — kicks off the
   * drag loop. The hook owns the document-level move/up listeners
   * and tears them down on pointer-up / abort.
   */
  readonly onPointerDown: (event: React.PointerEvent<HTMLElement>) => void;
  /** True while the user is actively dragging (for styling the handle). */
  readonly isDragging: boolean;
}

const _isFinitePositiveNumber = (n: unknown): n is number =>
  typeof n === "number" && Number.isFinite(n) && n >= 0;

function _readStored(key: string | undefined): number | null {
  if (!key || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return null;
    const parsed = Number.parseFloat(raw);
    return _isFinitePositiveNumber(parsed) ? parsed : null;
  } catch {
    // localStorage can throw in private windows / quota exhaustion —
    // treat any read failure as "no saved value" rather than crashing
    // the page.
    return null;
  }
}

function _writeStored(key: string | undefined, value: number): void {
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Same posture as the read: best-effort write, ignore failures.
  }
}

function _clamp(value: number, min: number, max: number): number {
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

export function useResizable({
  initial,
  min,
  max,
  storageKey,
  direction = "left-to-right",
}: UseResizableOptions): UseResizableResult {
  const [value, _setValue] = useState<number>(() =>
    _clamp(_readStored(storageKey) ?? initial, min, max),
  );
  const [isDragging, setIsDragging] = useState(false);

  // Refs to keep the active drag's starting state stable across
  // renders without retriggering effect setup.
  const dragStartRef = useRef<{
    pointerId: number;
    startClientX: number;
    startValue: number;
  } | null>(null);

  const setValue = useCallback(
    (next: number) => {
      const clamped = _clamp(next, min, max);
      _setValue(clamped);
      _writeStored(storageKey, clamped);
    },
    [min, max, storageKey],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      // Ignore non-primary buttons (right-click, middle-click) so
      // context menus / scroll wheels still work over the handle.
      if (event.button !== 0) return;
      event.preventDefault();
      const target = event.currentTarget;
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // Pointer capture can fail on some headless/test environments;
        // the document-level fallback below still works without it.
      }
      dragStartRef.current = {
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startValue: value,
      };
      setIsDragging(true);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [value],
  );

  // Document-level listeners are attached only while a drag is active.
  // ``isDragging`` flips back to false on pointer-up / cancel, which
  // tears them down and frees the body cursor + selection.
  useEffect(() => {
    if (!isDragging) return;
    const sign = direction === "right-to-left" ? -1 : 1;

    const handleMove = (e: PointerEvent) => {
      const drag = dragStartRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      const deltaX = (e.clientX - drag.startClientX) * sign;
      setValue(drag.startValue + deltaX);
    };
    const handleEnd = (e: PointerEvent) => {
      const drag = dragStartRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      dragStartRef.current = null;
      setIsDragging(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("pointermove", handleMove);
    document.addEventListener("pointerup", handleEnd);
    document.addEventListener("pointercancel", handleEnd);
    return () => {
      document.removeEventListener("pointermove", handleMove);
      document.removeEventListener("pointerup", handleEnd);
      document.removeEventListener("pointercancel", handleEnd);
    };
  }, [isDragging, direction, setValue]);

  return useMemo(
    () => ({ value, setValue, onPointerDown, isDragging }),
    [value, setValue, onPointerDown, isDragging],
  );
}
