/**
 * Kbd — small keyboard-hint pill (mono 10px, dropped-bottom border).
 *
 * Used inline next to actions to surface the keyboard equivalent, e.g.
 * "Validate <Kbd>v</Kbd>". Static — never interactive.
 */
import type { ReactElement, ReactNode } from "react";

export interface KbdProps {
  children: ReactNode;
}

export function Kbd({ children }: KbdProps): ReactElement {
  return <kbd className="orb-kbd">{children}</kbd>;
}
