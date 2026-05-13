/**
 * Coverage for the generic resize state machine used by both the
 * outer rail handle and the inner doc/objects handle in ``/kf/review``.
 *
 * We exercise the hook through ``renderHook`` and dispatch synthetic
 * pointer events at ``document`` level — the production code attaches
 * its move/up listeners there once a drag is in flight.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useResizable } from "./useResizable";

function _pointerEvent(type: string, init: PointerEventInit): PointerEvent {
  // jsdom doesn't ship ``PointerEvent``; build a ``MouseEvent`` and
  // tack on a ``pointerId`` so the production listeners see what they
  // expect at runtime.
  const ev = new MouseEvent(type, { bubbles: true, ...init }) as PointerEvent;
  Object.defineProperty(ev, "pointerId", { value: init.pointerId ?? 1 });
  return ev;
}

function _onPointerDown(
  result: { current: ReturnType<typeof useResizable> },
  clientX: number,
): void {
  const target = document.createElement("div");
  const event = {
    button: 0,
    clientX,
    pointerId: 1,
    currentTarget: target,
    preventDefault: vi.fn(),
  } as unknown as React.PointerEvent<HTMLElement>;
  // Stub ``setPointerCapture`` — jsdom doesn't implement it on plain
  // HTMLElements.
  (target as HTMLElement).setPointerCapture = vi.fn();
  act(() => {
    result.current.onPointerDown(event);
  });
}

describe("useResizable", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("starts at the initial value when nothing is stored", () => {
    const { result } = renderHook(() =>
      useResizable({ initial: 380, min: 200, max: 600 }),
    );
    expect(result.current.value).toBe(380);
    expect(result.current.isDragging).toBe(false);
  });

  it("hydrates from localStorage and clamps to [min, max]", () => {
    window.localStorage.setItem("kf:rail-w-test", "10000");
    const { result } = renderHook(() =>
      useResizable({
        initial: 380,
        min: 200,
        max: 600,
        storageKey: "kf:rail-w-test",
      }),
    );
    // Stored value (10000) is clamped down to max (600), not silently
    // accepted and stranded off-screen.
    expect(result.current.value).toBe(600);
  });

  it("setValue clamps to [min, max] and writes to localStorage", () => {
    const { result } = renderHook(() =>
      useResizable({
        initial: 380,
        min: 200,
        max: 600,
        storageKey: "kf:rail-w-test",
      }),
    );
    act(() => result.current.setValue(50));
    expect(result.current.value).toBe(200);
    expect(window.localStorage.getItem("kf:rail-w-test")).toBe("200");

    act(() => result.current.setValue(9999));
    expect(result.current.value).toBe(600);
    expect(window.localStorage.getItem("kf:rail-w-test")).toBe("600");
  });

  it("drag updates the value by the pointer-x delta", () => {
    const { result } = renderHook(() =>
      useResizable({ initial: 380, min: 200, max: 600 }),
    );
    _onPointerDown(result, 100);
    expect(result.current.isDragging).toBe(true);
    act(() => {
      document.dispatchEvent(_pointerEvent("pointermove", { clientX: 150 }));
    });
    // Delta +50px from start → 380 + 50 = 430.
    expect(result.current.value).toBe(430);

    act(() => {
      document.dispatchEvent(_pointerEvent("pointerup", { clientX: 150 }));
    });
    expect(result.current.isDragging).toBe(false);
  });

  it("reversed direction inverts the delta sign", () => {
    const { result } = renderHook(() =>
      useResizable({
        initial: 380,
        min: 200,
        max: 600,
        direction: "right-to-left",
      }),
    );
    _onPointerDown(result, 100);
    act(() => {
      document.dispatchEvent(_pointerEvent("pointermove", { clientX: 150 }));
    });
    // +50px delta with reversed sign → 380 - 50 = 330.
    expect(result.current.value).toBe(330);
  });

  it("ignores non-primary pointer buttons (right/middle click)", () => {
    const { result } = renderHook(() =>
      useResizable({ initial: 380, min: 200, max: 600 }),
    );
    const target = document.createElement("div");
    (target as HTMLElement).setPointerCapture = vi.fn();
    const event = {
      button: 2, // right click
      clientX: 100,
      pointerId: 1,
      currentTarget: target,
      preventDefault: vi.fn(),
    } as unknown as React.PointerEvent<HTMLElement>;
    act(() => result.current.onPointerDown(event));
    expect(result.current.isDragging).toBe(false);
  });

  it("pointerup ends the drag and frees the body cursor", () => {
    const { result } = renderHook(() =>
      useResizable({ initial: 380, min: 200, max: 600 }),
    );
    _onPointerDown(result, 100);
    expect(document.body.style.cursor).toBe("col-resize");
    act(() => {
      document.dispatchEvent(_pointerEvent("pointerup", { clientX: 100 }));
    });
    expect(result.current.isDragging).toBe(false);
    expect(document.body.style.cursor).toBe("");
    expect(document.body.style.userSelect).toBe("");
  });
});
