/**
 * Unit tests for the YAML emitter that backs the Explorer's "Export
 * taxonomy" button (issue #298, scope a).
 *
 * The output must be parseable by the backend ``taxonomy_loader``
 * (``yaml.safe_load`` + structural validation in
 * ``apps/api/app/services/taxonomy_loader.py``). These tests assert
 * the wire-level invariants the loader cares about: top-level
 * ``schema_version`` + ``categories`` keys, every category has
 * ``id`` / ``label`` / ``description``, ``subcategories`` is a list
 * (possibly empty), and special characters round-trip without
 * corrupting the YAML structure.
 *
 * We don't pull in ``js-yaml`` to verify here (the production code
 * doesn't either, on purpose — see ``taxonomy-export.ts`` rationale),
 * so the assertions are line-pattern rather than parse-then-compare.
 */

import { describe, expect, it } from "vitest";

import type { TaxonomyResponse } from "../api/types";
import {
  escapeYamlString,
  taxonomyExportFilename,
  taxonomyResponseToYaml,
} from "./taxonomy-export";

describe("escapeYamlString", () => {
  it("wraps simple ASCII in double quotes verbatim", () => {
    expect(escapeYamlString("hr")).toBe('"hr"');
    expect(escapeYamlString("Operator-authored HR.")).toBe('"Operator-authored HR."');
  });

  it("escapes backslashes and embedded double quotes", () => {
    expect(escapeYamlString('a"b')).toBe('"a\\"b"');
    expect(escapeYamlString("a\\b")).toBe('"a\\\\b"');
  });

  it("folds newlines, carriage returns, and tabs to escape sequences", () => {
    expect(escapeYamlString("line one\nline two")).toBe('"line one\\nline two"');
    expect(escapeYamlString("a\tb")).toBe('"a\\tb"');
    expect(escapeYamlString("crlf\r\n")).toBe('"crlf\\r\\n"');
  });

  it("hex-escapes other C0 control characters", () => {
    // ``\x07`` (bell) is a C0 control with no shorthand; it must be
    // emitted as ``\x07`` so YAML parsers don't choke.
    expect(escapeYamlString("")).toBe('"\\x07"');
  });
});

describe("taxonomyResponseToYaml", () => {
  const baseResponse = (categories: TaxonomyResponse["categories"]): TaxonomyResponse => ({
    schema_version: "v0.1",
    is_configured: true,
    source_path: "/etc/kw/taxonomy.yml",
    categories,
  });

  it("emits the schema_version + empty categories list when there is nothing to export", () => {
    const yaml = taxonomyResponseToYaml(baseResponse([]));
    expect(yaml).toContain('schema_version: "v0.1"');
    expect(yaml).toContain("categories: []");
  });

  it("includes a header comment + the source_path on the API host", () => {
    const yaml = taxonomyResponseToYaml(baseResponse([]));
    expect(yaml.split("\n")[0]).toBe("# Exported from KW Pipeline Knowledge Explorer");
    expect(yaml).toContain("# Source path on the API host: /etc/kw/taxonomy.yml");
  });

  it("emits each category with id, label, description, source, and an empty subcategories list", () => {
    const yaml = taxonomyResponseToYaml(
      baseResponse([
        {
          id: "hr",
          label: "HR",
          description: "Operator-authored HR.",
          subcategories: [],
          source: "imposed",
        },
      ]),
    );
    expect(yaml).toContain('  - id: "hr"');
    expect(yaml).toContain('    label: "HR"');
    expect(yaml).toContain('    description: "Operator-authored HR."');
    expect(yaml).toContain('    source: "imposed"');
    expect(yaml).toContain("    subcategories: []");
  });

  it("preserves the source flag for both halves of the hybrid taxonomy (imposed + computed)", () => {
    const yaml = taxonomyResponseToYaml(
      baseResponse([
        {
          id: "hr",
          label: "HR",
          description: "Operator-authored.",
          subcategories: [],
          source: "imposed",
        },
        {
          id: "topic-cluster-42",
          label: "Compliance memos",
          description: "Auto-deduced.",
          subcategories: [],
          source: "computed",
        },
      ]),
    );
    expect(yaml).toContain('source: "imposed"');
    expect(yaml).toContain('source: "computed"');
  });

  it("nests subcategories under their parent with the right indent depth", () => {
    const yaml = taxonomyResponseToYaml(
      baseResponse([
        {
          id: "hr",
          label: "HR",
          description: "Top.",
          source: "imposed",
          subcategories: [
            {
              id: "hr.hybrid_work",
              label: "Hybrid work",
              description: "Child.",
              source: "imposed",
              subcategories: [],
            },
          ],
        },
      ]),
    );
    // Top-level item lives at indent 1 (two spaces). Children lives
    // at indent 3 (six spaces) — i.e. inside the parent's
    // ``subcategories:`` sequence.
    expect(yaml).toContain('  - id: "hr"');
    expect(yaml).toContain("    subcategories:\n");
    expect(yaml).toContain('      - id: "hr.hybrid_work"');
    expect(yaml).toContain('        label: "Hybrid work"');
  });

  it("escapes descriptions with newlines / colons / quotes so the YAML stays valid", () => {
    const yaml = taxonomyResponseToYaml(
      baseResponse([
        {
          id: "tricky",
          label: "Tricky",
          // Newlines + colon + quote — all three break naive emitters.
          description: 'first line\nsecond: with "quotes"',
          subcategories: [],
          source: "imposed",
        },
      ]),
    );
    // Description must be one logical YAML line (no raw newline mid-scalar).
    expect(yaml).toContain('description: "first line\\nsecond: with \\"quotes\\""');
  });
});

describe("taxonomyExportFilename", () => {
  it("uses the UTC date and the .yaml suffix", () => {
    const fixed = new Date(Date.UTC(2026, 4, 7, 12, 34, 56));
    expect(taxonomyExportFilename(fixed)).toBe("kw-taxonomy-2026-05-07.yaml");
  });

  it("zero-pads single-digit months and days", () => {
    const fixed = new Date(Date.UTC(2026, 0, 3, 0, 0, 0));
    expect(taxonomyExportFilename(fixed)).toBe("kw-taxonomy-2026-01-03.yaml");
  });
});
