/**
 * Knowledge Forge banners — three site-wide alerts that stack at the
 * top of the main pane (design §9):
 *
 *   1. ForceAutoBanner       — auto-validation forced on (warn)
 *   2. SessionExpiredBanner  — 401 anywhere (err) — already in
 *                              `apps/_shared/auth`; re-exported here
 *                              for one-stop import.
 *   3. DeepLinkErrorBanner   — `?document=…` couldn't resolve (warn)
 *
 * Force-auto + deep-link banners dismiss per session via local state.
 * Session-expired forces a reload so it doesn't expose a dismiss
 * button.
 */

import { useState } from "react";
import type { ReactElement } from "react";

import { Btn, OrbI } from "../index";

export { SessionExpiredBanner } from "../../../../_shared/auth";

export interface ForceAutoBannerProps {
  /** Number of docs flagged in the trailing window. */
  flaggedCount?: number;
  /** Hide the banner — used when `/admin/config.force_auto_corpus` is false. */
  hidden?: boolean;
}

export function ForceAutoBanner({
  flaggedCount,
  hidden = false,
}: ForceAutoBannerProps): ReactElement | null {
  if (hidden) return null;
  return (
    <div className="kf-banner kf-banner--warn" role="status" data-testid="kf-banner-force-auto">
      <span className="kf-banner__icon" aria-hidden="true">
        {OrbI.alert}
      </span>
      <span className="kf-banner__msg">
        <strong>Auto-validation forced on by admin</strong>
        {typeof flaggedCount === "number" && flaggedCount > 0 && (
          <> · {flaggedCount} doc{flaggedCount === 1 ? "" : "s"} flagged in the last 24h</>
        )}
      </span>
    </div>
  );
}

export interface DeepLinkErrorBannerProps {
  /** The id that failed to resolve. */
  documentId: string;
  onDismiss?: () => void;
}

export function DeepLinkErrorBanner({
  documentId,
  onDismiss,
}: DeepLinkErrorBannerProps): ReactElement {
  return (
    <div
      className="kf-banner kf-banner--warn"
      role="alert"
      data-testid="kf-banner-deep-link"
    >
      <span className="kf-banner__icon" aria-hidden="true">
        {OrbI.alert}
      </span>
      <span className="kf-banner__msg">
        <code className="orb-mono">{documentId}</code> could not be resolved
        (404). Showing the catalog.
      </span>
      {onDismiss && (
        <Btn xs kind="ghost" onClick={onDismiss} aria-label="Dismiss deep link error">
          {OrbI.x}
        </Btn>
      )}
    </div>
  );
}

/**
 * Wrap site-wide banners — convenience composition for pages that
 * need to render the full stack without duplicating layout glue.
 */
export interface BannerStackProps {
  forceAutoFlaggedCount?: number;
  forceAutoOn?: boolean;
  deepLinkErrorId?: string | null;
}

export function BannerStack({
  forceAutoFlaggedCount,
  forceAutoOn = false,
  deepLinkErrorId,
}: BannerStackProps): ReactElement | null {
  const [dismissedDeepLink, setDismissedDeepLink] = useState(false);
  const showForceAuto = forceAutoOn;
  const showDeepLink = deepLinkErrorId && !dismissedDeepLink;
  if (!showForceAuto && !showDeepLink) return null;
  return (
    <div className="kf-banner-stack">
      {showForceAuto && (
        <ForceAutoBanner flaggedCount={forceAutoFlaggedCount} />
      )}
      {showDeepLink && (
        <DeepLinkErrorBanner
          documentId={deepLinkErrorId!}
          onDismiss={() => setDismissedDeepLink(true)}
        />
      )}
    </div>
  );
}
