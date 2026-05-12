/**
 * DocumentDetailCard — the right-column "Document detail" card on the
 * Review tab. Pure presentation; the parent passes a hydrated
 * `ApiDocument`. Stack of MetaRows: ID, Filename, Versions, Bytes,
 * Scope, Uploaded, Extractor, Semantic.
 */

import type { ReactElement } from "react";

import { Card, CardHead, MetaRow, SectionH } from "../index";
import type { ApiDocument } from "../../api/types";
import {
  distinctScopeKinds,
  formatBytes,
  latestVersion,
  splitIsoTimestamp,
} from "./format";

export interface DocumentDetailCardProps {
  document: ApiDocument | null;
}

export function DocumentDetailCard({
  document,
}: DocumentDetailCardProps): ReactElement {
  if (!document) {
    return (
      <Card>
        <CardHead>
          <SectionH>Document detail</SectionH>
        </CardHead>
        <div style={{ padding: "12px 14px", color: "var(--orb-fg-muted)" }}>
          Pick a document from the rail.
        </div>
      </Card>
    );
  }

  const ver = latestVersion(document);
  const { day, time } = splitIsoTimestamp(
    ver?.created_at ?? document.created_at,
  );
  const scopes = distinctScopeKinds(document);

  return (
    <Card>
      <CardHead
        right={
          <span className="orb-mono kf-card-hint">
            GET /documents/{document.id}
          </span>
        }
      >
        <SectionH>Document detail</SectionH>
      </CardHead>
      <div className="kf-detail__body">
        <MetaRow k="ID">{document.id}</MetaRow>
        <MetaRow k="Filename">{document.original_filename}</MetaRow>
        <MetaRow k="Versions">
          {document.versions.length}
          {ver && (
            <>
              {" · latest "}
              <span className="orb-mono">{ver.id}</span>
            </>
          )}
        </MetaRow>
        <MetaRow k="Bytes">{formatBytes(ver?.file_size ?? null)}</MetaRow>
        <MetaRow k="Scope">
          {scopes.length === 0 ? "—" : scopes.join(" + ")}
        </MetaRow>
        <MetaRow k="Uploaded">
          {day} {time ? `${time}Z` : ""}
        </MetaRow>
        <MetaRow k="Content-Type">{ver?.content_type ?? "—"}</MetaRow>
        <MetaRow k="SHA-256">
          <span className="orb-mono kf-detail__hash">
            {ver?.sha256 ?? "—"}
          </span>
        </MetaRow>
      </div>
    </Card>
  );
}
