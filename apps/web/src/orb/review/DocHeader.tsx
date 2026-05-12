/**
 * DocHeader — title strip at the top of the main pane in the Review
 * Workspace. Per design §3.3:
 *
 *   Documents › {doc.id} › {doc.filename}
 *   ─────────────────────────────────────────────────────────────────
 *   <h1>filename</h1>
 *   [StatusBadge] [id mono] [v{n} · {bytes} · {pages} pages] [scope chips] [pill]
 *                                              [Copy link] [Refresh]
 */

import type { ReactElement } from "react";

import { Btn, OrbI, ScopeChip, StatusBadge } from "../index";
import type { ApiDocument } from "../../api/types";
import {
  distinctScopeKinds,
  formatBytes,
  latestStatus,
  latestVersion,
} from "./format";

export interface DocHeaderProps {
  document: ApiDocument | null;
  /** Per-doc page-count, when known. The list endpoint omits it. */
  pages?: number | null;
  onCopyLink?: () => void;
  onRefresh?: () => void;
  /** Right-aligned projection-status pill (PR 4 wires real values). */
  projectionPill?: { text: string; tone?: "ok" | "warn" | "err" };
}

export function DocHeader({
  document,
  pages = null,
  onCopyLink,
  onRefresh,
  projectionPill,
}: DocHeaderProps): ReactElement {
  if (!document) {
    return (
      <header className="kf-doch kf-doch--empty" aria-busy="true">
        <div className="kf-doch__crumbs orb-mono">Documents</div>
        <h1 className="kf-doch__title">Pick a document from the rail</h1>
      </header>
    );
  }

  const status = latestStatus(document);
  const ver = latestVersion(document);
  const versions = document.versions.length;
  const bytes = formatBytes(ver?.file_size ?? null);
  const scopes = distinctScopeKinds(document);

  return (
    <header className="kf-doch">
      <div className="kf-doch__crumbs">
        <span>Documents</span>
        <span className="kf-doch__sep" aria-hidden="true">
          {OrbI.chev}
        </span>
        <span className="orb-mono kf-doch__id">{document.id}</span>
        <span className="kf-doch__sep" aria-hidden="true">
          {OrbI.chev}
        </span>
        <span className="kf-doch__cur">{document.original_filename}</span>
      </div>

      <div className="kf-doch__row">
        <div className="kf-doch__main">
          <h1 className="kf-doch__title" title={document.original_filename}>
            {document.original_filename}
          </h1>
          <div className="kf-doch__meta">
            <StatusBadge status={status} />
            <span className="orb-mono kf-doch__id-dim">{document.id}</span>
            <span className="kf-doch__dim">
              v{versions} · {bytes}
              {pages != null ? ` · ${pages} pages` : ""}
            </span>
            {scopes.map((s) => (
              <ScopeChip key={s} scope={s} />
            ))}
            {projectionPill && (
              <ProjectionPill
                text={projectionPill.text}
                tone={projectionPill.tone ?? "ok"}
              />
            )}
          </div>
        </div>

        <div className="kf-doch__actions">
          <Btn kind="ghost" icon={OrbI.link} onClick={onCopyLink}>
            Copy link
          </Btn>
          <Btn kind="ghost" icon={OrbI.refresh} onClick={onRefresh}>
            Refresh
          </Btn>
        </div>
      </div>
    </header>
  );
}

function ProjectionPill({
  text,
  tone,
}: {
  text: string;
  tone: "ok" | "warn" | "err";
}): ReactElement {
  const dot =
    tone === "warn"
      ? "var(--orb-warn)"
      : tone === "err"
        ? "var(--orb-err)"
        : "var(--orb-ok)";
  return (
    <span className="kf-doch__pill orb-mono">
      <span className="dot" style={{ background: dot }} aria-hidden="true" />
      {text}
    </span>
  );
}
