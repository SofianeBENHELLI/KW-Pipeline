/**
 * Per-type renderer for the original uploaded binary.
 *
 * Strategy: stay zero-dependency. Browsers natively render PDF, image,
 * HTML, plain text, and JSON inside `<iframe>` / `<embed>` / `<img>` —
 * no PDF.js, no docx-to-html shim. Office formats (Word/PowerPoint/
 * Excel) have no in-browser viewer, so we surface a download link plus
 * a hint that the structured panes (raw extraction + semantic) carry
 * the actual content.
 *
 * Keeping the viewer dumb (no fetch logic, no abort plumbing) is on
 * purpose — the parent owns the URL and the URL is a `GET` against
 * the backend's raw endpoint, so rendering is just a matter of
 * pointing the right element at it.
 */

import React from "react";

import { Icon } from "../components/icons";
import { type DocumentKind, KIND_LABELS } from "./document-kind";

interface Props {
  kind: DocumentKind;
  /** URL to the raw bytes (`GET /documents/{id}/versions/{vid}/raw`). */
  src: string;
  /** Original filename — surfaced in the download fallback. */
  filename: string;
  /** Bytes; rendered as a human-readable string in the fallback. */
  fileSize: number;
}

export const OriginalViewer: React.FC<Props> = ({ kind, src, filename, fileSize }) => {
  switch (kind) {
    case "pdf":
      return (
        <iframe
          className="kx-viewer__frame"
          title={`PDF preview of ${filename}`}
          src={src}
        />
      );
    case "image":
      return (
        <div className="kx-viewer__image-wrap">
          <img className="kx-viewer__image" src={src} alt={filename} />
        </div>
      );
    case "html":
      return (
        <iframe
          className="kx-viewer__frame"
          title={`HTML preview of ${filename}`}
          src={src}
          // The host's CSP will block scripts inside this iframe; that's
          // intentional for archived web content.
          sandbox="allow-same-origin"
        />
      );
    case "text":
    case "markdown":
    case "wiki":
    case "json":
      // Browsers happily render text/* and application/json inline; the
      // raw endpoint already serves with the correct Content-Type.
      return (
        <iframe
          className="kx-viewer__frame"
          title={`Text preview of ${filename}`}
          src={src}
        />
      );
    case "word":
    case "powerpoint":
    case "excel":
    case "binary":
    default:
      return <BinaryFallback kind={kind} src={src} filename={filename} fileSize={fileSize} />;
  }
};

const BinaryFallback: React.FC<{
  kind: DocumentKind;
  src: string;
  filename: string;
  fileSize: number;
}> = ({ kind, src, filename, fileSize }) => {
  const label = KIND_LABELS[kind];
  return (
    <div className="kx-viewer__binary">
      <span className="kx-viewer__binary-glyph" aria-hidden="true">
        <Icon name="docs" size={28} />
      </span>
      <div className="kx-viewer__binary-title">{label} document</div>
      <div className="kx-viewer__binary-body">
        Browsers can&apos;t render {label} files inline. Download the original
        to inspect it in its native app — the <strong>Raw extraction</strong>{" "}
        and <strong>Semantic synthesis</strong> panes alongside this viewer
        carry the parsed text and structured claims.
      </div>
      <a
        className="kw-btn kw-btn--primary"
        href={src}
        download={filename}
        rel="noreferrer"
      >
        Download {filename} ({formatBytes(fileSize)})
      </a>
    </div>
  );
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const idx = Math.min(units.length - 1, Math.floor(Math.log10(bytes) / 3));
  const value = bytes / Math.pow(1000, idx);
  return `${value.toFixed(value >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
}
