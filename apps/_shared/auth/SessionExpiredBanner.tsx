/**
 * Session-expired banner — surfaces a 401 from the backend (per
 * ADR-019 §5).
 *
 * Stateless / themeless on purpose: each frontend app provides its
 * own className styling so the banner blends with the host visual
 * rhythm (Vite reviewer / 3DDashboard widget tile / Explorer rail).
 *
 * The component itself never decides what "sign in again" means —
 * the parent owns the action callback, because the right thing to
 * do depends on the auth mode at runtime:
 *
 *   * ``KW_AUTH_MODE=dev`` (default per #245) — reload picks up a
 *     fresh dev user on the very next request.
 *   * ``KW_AUTH_MODE=bearer`` — reload bounces the user through
 *     whatever token-issuer flow they have wired (until the future
 *     refresh-token slice lands per ADR-019).
 *   * 3DEXPERIENCE host — reload semantically reloads the dashboard
 *     tile, which re-fires the host's auth handshake.
 *
 * In every case today the parent passes ``window.location.reload``
 * as ``onSignIn``; that single behavioural choice lives in the app's
 * root, not in this shared component.
 */

import React from "react";

export interface SessionExpiredBannerProps {
  /** True when the most recent API call returned 401. */
  visible: boolean;
  /**
   * Called when the user clicks the action button. The caller decides
   * what "sign in again" means at runtime (dev = reload, bearer =
   * reload + IdP bounce, 3DX = tile reload).
   */
  onSignIn: () => void;
  /** Optional override for the action label. Defaults to "Sign in again". */
  actionLabel?: string;
  /**
   * Optional class name applied to the outer banner ``<div>``. Each
   * host app passes its own theme hook here so the banner can borrow
   * the surrounding visual rhythm. Combined with the always-on
   * ``kw-session-expired`` className that the test suite hooks into.
   */
  className?: string;
}

/**
 * Pure presentational banner. ``role="alert"`` + ``aria-live="polite"``
 * together announce the expiry to assistive tech without yanking
 * keyboard focus the way ``aria-live="assertive"`` would.
 */
export const SessionExpiredBanner: React.FC<SessionExpiredBannerProps> = ({
  visible,
  onSignIn,
  actionLabel,
  className,
}) => {
  if (!visible) return null;
  const cls = ["kw-session-expired", className].filter(Boolean).join(" ");
  return (
    <div
      role="alert"
      aria-live="polite"
      className={cls}
      data-testid="session-expired-banner"
    >
      <div className="kw-session-expired__copy">
        <strong>Your session has expired.</strong>{" "}
        Sign in again to keep working — your in-progress draft is
        preserved.
      </div>
      <button
        type="button"
        onClick={onSignIn}
        className="kw-session-expired__action"
        data-testid="session-expired-banner-action"
      >
        {actionLabel ?? "Sign in again"}
      </button>
    </div>
  );
};
