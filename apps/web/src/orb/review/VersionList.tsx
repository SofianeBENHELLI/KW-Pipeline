/**
 * VersionList — last N versions with status badges + relative time.
 *
 * Per design §3.5: "last N versions w/ status badge + relative time".
 * The current version is flagged with `is-cur`. Renders a simple
 * dashed-rule list inside a Card.
 */

import type { ReactElement } from "react";

import { Card, CardHead, SectionH, StatusBadge } from "../index";
import type { ApiDocument, ApiDocumentVersion } from "../../api/types";

export interface VersionListProps {
  document: ApiDocument | null;
  /** Max rows to show. Default 4 (matches the prototype). */
  limit?: number;
}

export function VersionList({
  document,
  limit = 4,
}: VersionListProps): ReactElement {
  const versions = (document?.versions ?? [])
    .slice()
    .sort((a, b) => b.version_number - a.version_number)
    .slice(0, limit);
  const total = document?.versions.length ?? 0;
  const latestId = document?.latest_version_id ?? null;

  return (
    <Card>
      <CardHead
        right={
          <span className="orb-mono kf-card-hint">{total} total</span>
        }
      >
        <SectionH>Versions</SectionH>
      </CardHead>
      <div className="kf-versions__body">
        {versions.length === 0 && (
          <div className="kf-versions__empty">No versions yet.</div>
        )}
        {versions.map((v) => (
          <VersionRow key={v.id} version={v} isCurrent={v.id === latestId} />
        ))}
      </div>
    </Card>
  );
}

function VersionRow({
  version,
  isCurrent,
}: {
  version: ApiDocumentVersion;
  isCurrent: boolean;
}): ReactElement {
  return (
    <div className={`kf-versions__row ${isCurrent ? "is-cur" : ""}`}>
      <span className="orb-mono kf-versions__num">v{version.version_number}</span>
      <StatusBadge status={version.status} />
      <span className="orb-mono kf-versions__date">
        {(version.created_at ?? "").slice(0, 10) || "—"}
      </span>
    </div>
  );
}
