/**
 * Small pure helpers for surfacing API document fields in the rail/header.
 */

import type { ApiDocument, ApiDocumentVersion } from "../../api/types";

/** Latest version of a document, or `null` if the catalog row has none. */
export function latestVersion(
  doc: ApiDocument | null | undefined,
): ApiDocumentVersion | null {
  if (!doc) return null;
  // The API surfaces `latest_version_id`; pick the matching row, falling
  // back to the last in the array since some endpoints have shipped
  // un-flagged versions.
  if (doc.latest_version_id) {
    const found = doc.versions.find((v) => v.id === doc.latest_version_id);
    if (found) return found;
  }
  return doc.versions[doc.versions.length - 1] ?? null;
}

/** Status of the latest version, or `STORED` if no version exists yet. */
export function latestStatus(doc: ApiDocument | null | undefined): string {
  return latestVersion(doc)?.status ?? "STORED";
}

/**
 * Format a byte count as a short human string (e.g. 812 KB, 1.4 MB).
 *
 * Mirrors the prototype's hand-typed `bytes: "812 KB"` strings. The
 * threshold-based switch keeps the surface deterministic — no locale
 * formatting, so unit tests can pin the exact output.
 */
export function formatBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1).replace(/\.0$/, "")} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1).replace(/\.0$/, "")} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1).replace(/\.0$/, "")} GB`;
}

/**
 * Split an ISO-8601 timestamp into `{ day, time }` for the rail's
 * two-line uploaded column. Strict-no-Date: parse the string directly
 * so we don't depend on browser locale.
 */
export function splitIsoTimestamp(iso: string | null | undefined): {
  day: string;
  time: string;
} {
  if (!iso) return { day: "—", time: "" };
  // `2026-05-11T14:22:08Z` → ["2026-05-11", "14:22:08Z"] → "2026-05-11", "14:22"
  const [day = "—", rest = ""] = iso.split("T");
  const time = rest.replace(/Z$/, "").slice(0, 5); // HH:MM
  return { day, time };
}

/**
 * Map the backend scope kind onto the design-system token used by
 * `<ScopeChip>` (`personal | community | project`). `swym_community`
 * is the on-the-wire enum; the design vocabulary just calls it
 * "community", which is what `ScopeChip` expects.
 */
export function scopeKindToChipScope(
  kind: string,
): "personal" | "community" | "project" {
  if (kind === "project") return "project";
  if (kind === "swym_community" || kind === "community") return "community";
  return "personal";
}

/**
 * Distinct chip-friendly scope kinds for a document, dedup'd. The API
 * returns one row per scope link so a doc with both project + community
 * shows up twice; the rail wants one chip per kind.
 */
export function distinctScopeKinds(
  doc: ApiDocument | null | undefined,
): Array<"personal" | "community" | "project"> {
  if (!doc) return [];
  const seen = new Set<"personal" | "community" | "project">();
  for (const s of doc.scopes ?? []) {
    const mapped = scopeKindToChipScope(s.kind);
    if (!seen.has(mapped)) seen.add(mapped);
  }
  return [...seen];
}
