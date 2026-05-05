/**
 * Force-auto corpus banner (EPIC-A A.8, ADR-023 §6, #215).
 *
 * Renders a non-dismissible alert at the app root whenever
 * ``hitl.force_auto_corpus === true`` on the ``/admin/config``
 * payload — that flag means every version is being auto-validated
 * regardless of confidence score, OCR override, or SPC sampling. It's
 * a load-bearing override (used for backfill / corpus-replay runs)
 * and an operator running a pilot needs to see it at a glance.
 *
 * Non-dismissible on purpose: this is a config alert, not a user
 * notification. The remediation is "set ``KW_HITL_FORCE_AUTO_CORPUS``
 * to false and restart the API" — the operator clears it, not the
 * end user.
 *
 * Hidden when ``/admin/config`` returns 403 (caller is not admin) —
 * non-admin users don't see admin alerts. Also hidden on any other
 * fetch failure: the banner is informational and a fetch hiccup
 * shouldn't block the rest of the app.
 */

import React from "react";

export interface ForceAutoCorpusBannerProps {
  /** True when the admin-config response says force_auto_corpus is on. */
  visible: boolean;
}

/**
 * Pure presentational banner. ``role="alert"`` + ``aria-live="polite"``
 * announce the state change to assistive tech without yanking
 * keyboard focus the way ``aria-live="assertive"`` would.
 */
export const ForceAutoCorpusBanner: React.FC<ForceAutoCorpusBannerProps> = ({
  visible,
}) => {
  if (!visible) return null;
  return (
    <div
      role="alert"
      aria-live="polite"
      className="kw-corpus-alert"
      data-testid="force-auto-corpus-banner"
    >
      <strong>⚠ Force-auto mode is active</strong>
      <span>
        — every version is being auto-validated regardless of confidence
        score. Review the <code>KW_HITL_FORCE_AUTO_CORPUS</code>{" "}
        environment variable.
      </span>
    </div>
  );
};
