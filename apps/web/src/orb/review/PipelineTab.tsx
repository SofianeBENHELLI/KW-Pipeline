/**
 * PipelineTab — lifecycle history timeline for a document.
 *
 * Per design §3.6: "shows every state transition with the actor,
 * action, and timestamp." Uses the version list as the source of
 * truth — every version row is one transition entry. PR 4.x will
 * extend this with audit-event rows when we wire `/admin/audit` per
 * doc.
 */

import type { ReactElement } from "react";

import { Card, CardHead, SectionH, StatusBadge } from "../index";
import type { ApiDocument } from "../../api/types";
import { splitIsoTimestamp } from "./format";

export interface PipelineTabProps {
  document: ApiDocument | null;
}

export function PipelineTab({ document }: PipelineTabProps): ReactElement {
  if (!document) {
    return (
      <Card>
        <CardHead>
          <SectionH>Lifecycle history</SectionH>
        </CardHead>
        <div className="kf-pipeline__empty">
          Pick a document from the rail to see its lifecycle history.
        </div>
      </Card>
    );
  }

  const versions = [...document.versions].sort(
    (a, b) => b.version_number - a.version_number,
  );

  return (
    <Card>
      <CardHead
        right={
          <span className="orb-mono kf-card-hint">
            {versions.length} transitions
          </span>
        }
      >
        <SectionH>Lifecycle history</SectionH>
      </CardHead>
      <ol className="kf-pipeline__list" data-testid="kf-pipeline-list">
        {versions.map((v, idx) => {
          const { day, time } = splitIsoTimestamp(v.created_at);
          return (
            <li key={v.id} className="kf-pipeline__row">
              <span className="kf-pipeline__bullet" aria-hidden="true" />
              <div className="kf-pipeline__main">
                <div className="kf-pipeline__line">
                  <span className="orb-mono kf-pipeline__ver">
                    v{v.version_number}
                  </span>
                  <StatusBadge status={v.status} />
                  <span className="kf-pipeline__filename">{v.filename}</span>
                </div>
                <div className="kf-pipeline__meta orb-mono">
                  {day} {time}Z · {v.id}
                  {v.reviewer_note ? ` · note: ${v.reviewer_note}` : ""}
                  {v.failure_reason ? ` · failed: ${v.failure_reason}` : ""}
                </div>
              </div>
              {idx === 0 && (
                <span className="kf-pipeline__cur orb-mono">latest</span>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
