/**
 * BatchBanner — appears below the main Review grid while a batch
 * pipeline run is in flight. Per design §3.7: "{done} done · {failed}
 * failed · {in-flight} in-flight" plus a dismiss link, with failed
 * rows expanded into one-line errors.
 */

import type { ReactElement } from "react";

import { OrbI } from "../index";
import type {
  BatchSnapshot,
  BatchStage,
} from "../hooks/useBatchPipeline";

export interface BatchBannerProps {
  snapshot: BatchSnapshot | null;
  onDismiss: () => void;
}

export function BatchBanner({
  snapshot,
  onDismiss,
}: BatchBannerProps): ReactElement | null {
  if (!snapshot) return null;
  const stages = [...snapshot.progress.values()];
  const done = stages.filter((s) => s === "done").length;
  const failed = stages.filter((s) => s === "failed").length;
  const inFlight = stages.filter(
    (s) => s !== "done" && s !== "failed",
  ).length;

  return (
    <div className="kf-batchbanner" role="status" data-testid="kf-batchbanner">
      <div className="kf-batchbanner__h">
        <span className="kf-batchbanner__icon" aria-hidden="true">
          {OrbI.bolt}
        </span>
        <strong>Batch pipeline</strong>
        <span className="orb-mono kf-batchbanner__counts">
          {done} done · {failed} failed · {inFlight} in-flight
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="kf-batchbanner__dismiss"
          onClick={onDismiss}
        >
          dismiss
        </button>
      </div>
      {snapshot.failures.length > 0 && (
        <div className="kf-batchbanner__fail">
          {snapshot.failures.map((f) => (
            <div key={f.docId} className="orb-mono">
              <span style={{ color: "var(--orb-err)" }}>✗</span> {f.docId} ·{" "}
              <span style={{ color: "var(--orb-fg-muted)" }}>{f.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Per-doc stage label for the rail row when batch is in flight. */
export function batchStageLabel(stage: BatchStage): string {
  return stage;
}
