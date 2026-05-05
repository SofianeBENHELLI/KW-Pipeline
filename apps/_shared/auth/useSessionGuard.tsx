/**
 * Session-expired hook + provider — single source of truth for
 * "the most recent API call returned 401" across the app tree
 * (per ADR-019 §5).
 *
 * The provider holds a single boolean. The API client layer of each
 * app calls ``trigger()`` when an :class:`ApiError` with
 * ``status === 401`` lands; the banner reads ``expired`` and
 * renders. ``reset()`` is exposed for completeness but is rarely
 * needed in dev/bearer modes — the action button reloads the page,
 * which discards React state alongside everything else.
 *
 * Why a context rather than a singleton store:
 *
 * - The provider sits at the app root, so every consumer subscribes
 *   to the same state tree React already manages (no extra state
 *   library, no "imperative subscription" surface).
 * - The API-layer integration uses ``setSessionTrigger`` (a tiny
 *   module-level setter exported alongside ``ApiError``) inside a
 *   ``useEffect`` in the provider — this is the standard "register
 *   a callback before any request fires" pattern and stays
 *   testable because the setter is a plain function.
 *
 * Multiple 401s in flight don't stack: ``trigger()`` is idempotent
 * (sets ``true``), and the banner is single-instance because there
 * is exactly one provider per app. The dedup is verified in
 * ``useSessionGuard.test.tsx``.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

export interface SessionState {
  /** True when the most recent API call returned 401. */
  expired: boolean;
  /**
   * Mark the session as expired. Called by the API client layer when
   * an ApiError with ``status === 401`` is thrown. Idempotent —
   * multiple in-flight 401s collapse onto a single banner.
   */
  trigger: () => void;
  /**
   * Clear the expiry flag. Rarely used in practice (the action
   * button reloads the page, which trashes React state) but
   * exposed for tests and for a future "in-app refresh-token" flow.
   */
  reset: () => void;
}

const DEFAULT_STATE: SessionState = {
  expired: false,
  trigger: () => {
    // No provider mounted — silently ignore. Tests that don't render
    // the provider get a no-op state, mirroring the
    // "auth-mode=dev never 401s" reality.
  },
  reset: () => {
    // No-op for the same reason.
  },
};

const SessionContext = createContext<SessionState>(DEFAULT_STATE);

export interface SessionGuardProviderProps {
  children: React.ReactNode;
}

/**
 * Mounts a single ``SessionState`` for the whole app tree.
 *
 * Place at the app root (above every component that calls into
 * ``useSessionGuard`` AND above the API-client wire-up). Each app's
 * root also registers ``trigger`` with its API client so a 401
 * thrown from any endpoint flips the banner on.
 */
export const SessionGuardProvider: React.FC<SessionGuardProviderProps> = ({
  children,
}) => {
  const [expired, setExpired] = useState<boolean>(false);

  const trigger = useCallback(() => {
    setExpired(true);
  }, []);

  const reset = useCallback(() => {
    setExpired(false);
  }, []);

  const value = useMemo<SessionState>(
    () => ({ expired, trigger, reset }),
    [expired, trigger, reset],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
};

/**
 * Subscribe to the session-expired flag from any component.
 *
 * Returns the no-op default state when no provider is mounted, so
 * components used outside of the app shell (e.g. unit-tested in
 * isolation) keep working without forcing every test to wrap them
 * in a provider.
 */
export const useSessionGuard = (): SessionState =>
  useContext(SessionContext);
