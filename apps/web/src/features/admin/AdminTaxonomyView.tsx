/**
 * Admin UI — Taxonomy versioning lifecycle view (EPIC-1 §1.9, ADR-018).
 *
 * Read-only operator surface over the
 * ``/admin/taxonomy/versions/{taxonomy_id}`` lineage. The view exposes
 * the lifecycle state of every version in a taxonomy family as a
 * lineage panel: Draft 1 → Candidate V0 → Validated V1 → Archived.
 * Each row shows the version's state badge, label, audit metadata
 * (created_by, state_changed_at), suggestion / category counts, and
 * the dispatch path for the version's downstream consumers.
 *
 * Scope of slice 1.9:
 *
 * - Lookup by ``taxonomy_id`` (URL query param ``?taxonomy_id=`` or
 *   an in-page lookup form).
 * - Lineage list rendering with a state badge per row.
 * - State-machine context blurb under the header so an operator who
 *   landed without prior taxonomy training sees the legal moves.
 *
 * Explicitly out of scope (queued for follow-up slices):
 *
 * - Transition actions (promote / validate / archive / discard).
 * - Concept-suggestion review surface.
 * - Synthesize button (consumes #477's ``POST .../synthesize`` route).
 *
 * Auth posture follows the rest of ``/admin/*`` — the API gates with
 * ``require_admin``; the UI doesn't probe role client-side and a 403
 * envelope collapses the page to a "Forbidden" state.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, listTaxonomyVersions } from "../../api/client";
import type { ApiTaxonomyState, ApiTaxonomyVersion } from "../../api/types";

// ─── State badge ────────────────────────────────────────────────────────────

/** Short human-readable label for each state. The ADR-018 state names
 *  are clear enough to use directly, but the UI swaps in a marginally
 *  friendlier form (``Validated V1`` → ``Validated`` because the V1
 *  suffix isn't a meaningful distinction in the operator-facing list). */
const STATE_LABELS: Record<ApiTaxonomyState, string> = {
  DRAFT: "Draft",
  CANDIDATE_V0: "Candidate",
  VALIDATED_V1: "Validated",
  ARCHIVED: "Archived",
  DISCARDED: "Discarded",
};

/** Map state → CSS modifier class. Kept off the state string itself so
 *  the class name is stable even if the backend's literal changes. */
const STATE_MODIFIERS: Record<ApiTaxonomyState, string> = {
  DRAFT: "state-pill--draft",
  CANDIDATE_V0: "state-pill--candidate",
  VALIDATED_V1: "state-pill--validated",
  ARCHIVED: "state-pill--archived",
  DISCARDED: "state-pill--discarded",
};

interface StatePillProps {
  state: ApiTaxonomyState;
}

export function StatePill({ state }: StatePillProps) {
  return (
    <span
      className={`state-pill ${STATE_MODIFIERS[state]}`}
      data-testid={`state-pill-${state}`}
    >
      {STATE_LABELS[state]}
    </span>
  );
}

// ─── Timestamp helper ───────────────────────────────────────────────────────

/** ``2026-05-16T12:34:56Z`` → ``2026-05-16 12:34 UTC``. Keeps the
 *  display dense (the panel renders a column per version) while the
 *  full ISO stays on the cell's ``title`` for copy-paste workflows. */
export function formatTimestamp(isoString: string): string {
  const then = new Date(isoString);
  if (Number.isNaN(then.getTime())) return isoString;
  const yyyy = then.getUTCFullYear();
  const mm = String(then.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(then.getUTCDate()).padStart(2, "0");
  const hh = String(then.getUTCHours()).padStart(2, "0");
  const min = String(then.getUTCMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min} UTC`;
}

// ─── Category counter ───────────────────────────────────────────────────────

/** Recursive depth-aware count for the lineage row's
 *  ``category_count`` cell. Mirrors the backend's ``_count_categories``
 *  helper so the UI shows the same number the synthesize audit event
 *  records. */
function countCategories(
  // The schema's recursive types are awkward to import; ``unknown`` here
  // keeps the helper insensitive to the precise shape and lets us walk
  // ``subcategories`` defensively.
  categories: ReadonlyArray<{ subcategories?: unknown }>,
): number {
  let total = categories.length;
  for (const cat of categories) {
    if (Array.isArray(cat.subcategories)) {
      total += countCategories(
        cat.subcategories as ReadonlyArray<{ subcategories?: unknown }>,
      );
    }
  }
  return total;
}

// ─── Main view ──────────────────────────────────────────────────────────────

export function AdminTaxonomyView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialId = searchParams.get("taxonomy_id") ?? "";

  // Pending = the input value being typed. Applied = the id the table
  // currently reflects. The split keeps the URL stable while an
  // operator is mid-typing and matches the AdminAuditView pattern.
  const [pendingId, setPendingId] = useState(initialId);
  const [appliedId, setAppliedId] = useState(initialId);

  const [versions, setVersions] = useState<ApiTaxonomyVersion[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<ApiError | string | null>(null);

  const loadVersions = useCallback(async (taxonomyId: string) => {
    if (!taxonomyId) {
      setVersions(null);
      setLoadError(null);
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      const response = await listTaxonomyVersions(taxonomyId);
      setVersions(response.versions);
    } catch (err: unknown) {
      if (err instanceof ApiError) setLoadError(err);
      else if (err instanceof Error) setLoadError(err.message);
      else setLoadError("Failed to load taxonomy versions.");
      setVersions(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load whenever the applied id changes (including on first mount
  // when ``?taxonomy_id=`` is present).
  useEffect(() => {
    void loadVersions(appliedId);
  }, [appliedId, loadVersions]);

  const handleApply = useCallback(() => {
    setAppliedId(pendingId);
    if (pendingId) {
      setSearchParams({ taxonomy_id: pendingId });
    } else {
      setSearchParams({});
    }
  }, [pendingId, setSearchParams]);

  // Highlight the currently-active row (the highest-numbered
  // VALIDATED_V1, or DRAFT/CANDIDATE if none is validated yet). Lets
  // an operator see "this is the version a chunk classification would
  // currently use" without parsing the whole list.
  const activeVersionNumber = useMemo<number | null>(() => {
    if (!versions || versions.length === 0) return null;
    const validated = versions.filter((v) => v.state === "VALIDATED_V1");
    if (validated.length > 0) {
      return validated[validated.length - 1]!.version_number;
    }
    const candidate = versions.filter((v) => v.state === "CANDIDATE_V0");
    if (candidate.length > 0) {
      return candidate[candidate.length - 1]!.version_number;
    }
    const draft = versions.filter((v) => v.state === "DRAFT");
    if (draft.length > 0) return draft[draft.length - 1]!.version_number;
    return null;
  }, [versions]);

  // 403 — same pattern as AdminArchiveView / AdminHITLView / AdminAuditView.
  if (loadError instanceof ApiError && loadError.status === 403) {
    return (
      <main className="app-shell admin-shell" aria-label="Admin taxonomy">
        <section className="workspace">
          <header className="workspace-header">
            <h2>Forbidden</h2>
          </header>
          <p>
            This view requires the <code>admin</code> role.
          </p>
          <p className="muted">{loadError.detail}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell admin-shell" aria-label="Admin taxonomy">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Taxonomy versions</h2>
            <p className="muted">
              Drives the DRAFT → CANDIDATE → VALIDATED → ARCHIVED lifecycle for
              a taxonomy lineage (ADR-018). Look up a taxonomy by id to see
              every version, its state, and the audit metadata. Active version
              (the one chunk classification currently uses) is highlighted.
            </p>
          </div>
        </header>

        <form
          className="taxonomy-lookup"
          onSubmit={(e) => {
            e.preventDefault();
            handleApply();
          }}
        >
          <label htmlFor="taxonomy-id-input" className="muted">
            Taxonomy ID
          </label>
          <input
            id="taxonomy-id-input"
            type="text"
            value={pendingId}
            onChange={(e) => setPendingId(e.target.value)}
            placeholder="e.g. tx-2026-q1"
            data-testid="taxonomy-id-input"
          />
          <button
            type="submit"
            className="primary-button"
            data-testid="taxonomy-lookup-submit"
            disabled={loading}
          >
            {loading ? "Loading…" : "Look up"}
          </button>
        </form>

        {loadError instanceof ApiError && loadError.status !== 403 && (
          <div className="notice danger" role="alert">
            <strong>Failed to load taxonomy versions.</strong>
            <span>{loadError.detail}</span>
            {loadError.remediation && (
              <span className="muted">{loadError.remediation}</span>
            )}
          </div>
        )}
        {typeof loadError === "string" && (
          <div className="notice danger" role="alert">
            <strong>Failed to load taxonomy versions.</strong>
            <span>{loadError}</span>
          </div>
        )}

        {!appliedId && (
          <p className="muted" data-testid="taxonomy-empty-state">
            Enter a taxonomy id to view its version lineage.
          </p>
        )}

        {appliedId && versions !== null && versions.length === 0 && (
          <p className="muted" data-testid="taxonomy-no-versions">
            No versions found for <code>{appliedId}</code>. The id may be
            unknown, or the taxonomy lineage has not been seeded yet.
          </p>
        )}

        {appliedId && versions !== null && versions.length > 0 && (
          <table
            className="admin-table taxonomy-lineage-table"
            data-testid="taxonomy-lineage-table"
          >
            <caption className="visually-hidden">
              Taxonomy versions for {appliedId}
            </caption>
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">State</th>
                <th scope="col">Label</th>
                <th scope="col">Categories</th>
                <th scope="col">Suggestions</th>
                <th scope="col">Created by</th>
                <th scope="col">State changed</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr
                  key={v.version_number}
                  className={
                    v.version_number === activeVersionNumber
                      ? "taxonomy-lineage-row-active"
                      : undefined
                  }
                  data-testid={`taxonomy-lineage-row-${v.version_number}`}
                  aria-current={
                    v.version_number === activeVersionNumber
                      ? "true"
                      : undefined
                  }
                >
                  <td className="lineage-version-number">
                    v{v.version_number}
                  </td>
                  <td>
                    <StatePill state={v.state} />
                  </td>
                  <td>{v.version_label ?? <span className="muted">—</span>}</td>
                  <td>{countCategories(v.taxonomy.categories)}</td>
                  <td>{v.suggestions.length}</td>
                  <td>{v.created_by ?? <span className="muted">—</span>}</td>
                  <td title={v.state_changed_at}>
                    {formatTimestamp(v.state_changed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
