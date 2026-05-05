/**
 * Public exports for ``apps/_shared/auth``.
 *
 * The session-expired UX (#83 slice 3, ADR-019 §5) lives here so
 * the three frontend apps share one banner component, one provider,
 * and one hook surface. App-side wiring stays in each app's root
 * (where the api/client.ts module-level trigger is registered).
 */

export {
  SessionExpiredBanner,
  type SessionExpiredBannerProps,
} from "./SessionExpiredBanner";

export {
  SessionGuardProvider,
  useSessionGuard,
  type SessionGuardProviderProps,
  type SessionState,
} from "./useSessionGuard";
