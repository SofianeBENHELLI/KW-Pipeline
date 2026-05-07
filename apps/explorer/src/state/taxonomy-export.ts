/**
 * YAML serializer for the hybrid taxonomy (issue #298, scope a).
 *
 * The Explorer's "Export taxonomy" button reads
 * ``GET /knowledge/taxonomy`` (returns the merged imposed + computed
 * tree, ADR-017) and writes it back as YAML matching the format the
 * backend ``taxonomy_loader`` accepts via ``KW_TAXONOMY_PATH``. That
 * lets an operator round-trip the auto-deduced taxonomy: export →
 * tweak in any text editor → save under ``KW_TAXONOMY_PATH`` → API
 * picks the YAML up at next boot, promoting those categories from
 * ``"computed"`` to ``"imposed"``.
 *
 * We hand-roll the serializer rather than pull in ``js-yaml`` for one
 * button. The taxonomy shape is constrained (id, label, description,
 * source, subcategories — every leaf string), so a 30-line emitter is
 * smaller and auditable than a generic library. Strings are emitted
 * as YAML double-quoted scalars (RFC-compliant escapes for ``"`` and
 * ``\``) which is the safest form for free-text descriptions
 * containing colons, hashes, or multi-line content.
 */

import type { TaxonomyCategory, TaxonomyResponse } from "../api/types";

/**
 * Escape a string for emission as a YAML double-quoted scalar.
 *
 * Per the YAML 1.2 spec §5.7, the only required escapes inside
 * double-quotes are ``\``, ``"``, and the C0 control characters.
 * We also fold ``\n`` / ``\r`` / ``\t`` to keep the output one line
 * per scalar — multi-line descriptions stay readable while still
 * round-tripping byte-for-byte through ``yaml.safe_load``.
 */
export function escapeYamlString(value: string): string {
  let out = '"';
  for (const ch of value) {
    if (ch === "\\") out += "\\\\";
    else if (ch === '"') out += '\\"';
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch.charCodeAt(0) < 0x20) {
      out += "\\x" + ch.charCodeAt(0).toString(16).padStart(2, "0");
    } else {
      out += ch;
    }
  }
  return out + '"';
}

function indent(level: number): string {
  return "  ".repeat(level);
}

function serializeCategory(category: TaxonomyCategory, level: number): string {
  const pad = indent(level);
  const childPad = indent(level + 1);
  // ``- id:`` opens the list-item; subsequent fields align under the
  // same indent (one extra level so they read as siblings of ``id``).
  let out = "";
  out += `${pad}- id: ${escapeYamlString(category.id)}\n`;
  out += `${childPad}label: ${escapeYamlString(category.label)}\n`;
  out += `${childPad}description: ${escapeYamlString(category.description)}\n`;
  // ``source`` is optional in the wire shape; emit it explicitly so
  // the export captures the imposed/computed split per the issue's
  // "preserve the source field" requirement.
  if (category.source) {
    out += `${childPad}source: ${escapeYamlString(category.source)}\n`;
  }
  if (category.subcategories.length === 0) {
    out += `${childPad}subcategories: []\n`;
  } else {
    out += `${childPad}subcategories:\n`;
    for (const child of category.subcategories) {
      out += serializeCategory(child, level + 2);
    }
  }
  return out;
}

/**
 * Serialize a ``TaxonomyResponse`` to a YAML document the backend
 * loader accepts. Round-trips through ``yaml.safe_load`` →
 * ``_parse_root`` (flat-root form, the loader also accepts the
 * ``taxonomy:`` wrapped form but flat is canonical in the ADR).
 */
export function taxonomyResponseToYaml(response: TaxonomyResponse): string {
  let out = "";
  out += "# Exported from KW Pipeline Knowledge Explorer\n";
  if (response.source_path) {
    out += `# Source path on the API host: ${response.source_path}\n`;
  }
  out += `schema_version: ${escapeYamlString(response.schema_version)}\n`;
  if (response.categories.length === 0) {
    out += "categories: []\n";
    return out;
  }
  out += "categories:\n";
  for (const category of response.categories) {
    out += serializeCategory(category, 1);
  }
  return out;
}

/**
 * Build a stable filename for the download. Includes a UTC date stamp
 * so successive exports don't overwrite each other in the operator's
 * Downloads folder.
 */
export function taxonomyExportFilename(now: Date = new Date()): string {
  const yyyy = now.getUTCFullYear().toString().padStart(4, "0");
  const mm = (now.getUTCMonth() + 1).toString().padStart(2, "0");
  const dd = now.getUTCDate().toString().padStart(2, "0");
  return `kw-taxonomy-${yyyy}-${mm}-${dd}.yaml`;
}

/**
 * Trigger a browser download for the given YAML text. Split out from
 * the React component so the component test can stub it cleanly.
 */
export function triggerYamlDownload(yamlText: string, filename: string): void {
  const blob = new Blob([yamlText], { type: "application/x-yaml" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  // Some browsers require the anchor to be in the DOM for ``.click()``
  // to dispatch the download; mount, click, unmount.
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
