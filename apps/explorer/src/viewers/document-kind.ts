/**
 * Map a (content_type, filename) pair onto the viewer family the Document
 * page should mount. The Knowledge Explorer stores its routing decisions
 * here so the viewer registry stays a single switch instead of being
 * scattered across components.
 *
 * The widget is intentionally permissive: anything we can't classify
 * lands on the generic "binary" viewer (download link). This way an
 * unsupported file still renders a usable page rather than a runtime
 * crash.
 */

export type DocumentKind =
  | "pdf"
  | "word"
  | "powerpoint"
  | "excel"
  | "image"
  | "text"
  | "markdown"
  | "wiki"
  | "html"
  | "json"
  | "binary";

const CT_MAP: Array<[RegExp, DocumentKind]> = [
  [/^application\/pdf$/i, "pdf"],
  [/wordprocessingml|msword|application\/vnd\.ms-word/i, "word"],
  [/presentationml|powerpoint|vnd\.ms-powerpoint/i, "powerpoint"],
  [/spreadsheetml|excel|vnd\.ms-excel/i, "excel"],
  [/^image\//i, "image"],
  [/^application\/json$/i, "json"],
  [/^text\/markdown|^text\/x-markdown/i, "markdown"],
  [/^text\/html/i, "html"],
  [/^text\//i, "text"],
];

const EXT_MAP: Record<string, DocumentKind> = {
  pdf: "pdf",
  doc: "word",
  docx: "word",
  ppt: "powerpoint",
  pptx: "powerpoint",
  xls: "excel",
  xlsx: "excel",
  csv: "excel",
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  webp: "image",
  svg: "image",
  md: "markdown",
  markdown: "markdown",
  wiki: "wiki",
  mediawiki: "wiki",
  html: "html",
  htm: "html",
  json: "json",
  txt: "text",
  log: "text",
  rst: "text",
  yml: "text",
  yaml: "text",
};

export function classifyDocument(
  contentType: string | null | undefined,
  filename: string | null | undefined,
): DocumentKind {
  const ct = (contentType ?? "").trim();
  for (const [pattern, kind] of CT_MAP) {
    if (pattern.test(ct)) return kind;
  }
  const name = (filename ?? "").toLowerCase();
  const dot = name.lastIndexOf(".");
  if (dot >= 0 && dot < name.length - 1) {
    const ext = name.slice(dot + 1);
    if (ext in EXT_MAP) return EXT_MAP[ext];
  }
  return "binary";
}

export const KIND_LABELS: Record<DocumentKind, string> = {
  pdf: "PDF",
  word: "Word",
  powerpoint: "PowerPoint",
  excel: "Spreadsheet",
  image: "Image",
  text: "Plain text",
  markdown: "Markdown",
  wiki: "Wiki",
  html: "HTML",
  json: "JSON",
  binary: "Binary",
};
