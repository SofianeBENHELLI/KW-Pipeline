/**
 * Document-type glyph used in the Recent Documents list. A small
 * dog-eared rectangle with the file extension printed inside, in
 * the spirit of the design handoff `.hf-doc .ico` block. Pure CSS
 * shape — no SVG — so it scales with the surrounding text.
 */

import React from "react";

interface Props {
  /** File extension (without the dot), e.g. `pdf`. Lowercased for display. */
  ext: string;
}

export const FileTypeIcon: React.FC<Props> = ({ ext }) => {
  const display = (ext || "").trim().toLowerCase().slice(0, 4);
  return (
    <span className="kw-file-icon" aria-hidden="true">
      <span className="kw-file-icon__ext">{display}</span>
    </span>
  );
};

/**
 * Best-effort extension extraction from a filename. Falls back to the
 * empty string when the filename has no extension.
 */
export function extOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot < 0 || dot === filename.length - 1) return "";
  return filename.slice(dot + 1);
}
