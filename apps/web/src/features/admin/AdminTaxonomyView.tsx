/**
 * Admin UI — Taxonomy versioning lifecycle view (EPIC-1 §1.9, ADR-018).
 *
 * Operator surface over the
 * ``/admin/taxonomy/versions/{taxonomy_id}`` lineage. Reads the
 * lineage, surfaces the lifecycle state of every version, and (slice 3
 * follow-up) drives the DRAFT → CANDIDATE → VALIDATED → ARCHIVED
 * transitions inline so the operator never has to leave the page to
 * curl a route.
 *
 * Scope today:
 *
 * - Lookup by ``taxonomy_id`` (URL query param or in-page form).
 * - Lineage list with state-coloured pills.
 * - State-machine-gated transition buttons per row: Promote / Validate /
 *   Archive / Discard. Synthesize is rendered as a disabled stub until
 *   #477 lands the route in the OpenAPI schema (the slice 3 audit
 *   flagged this as the only "no backend yet" case).
 * - Create-draft modal that mints a new lineage or branches an
 *   existing one.
 * - Per-row Concepts panel: the version's ``suggestions[]`` rendered
 *   as an inline table with Accept / Reject / Defer / Merge actions
 *   (Merge needs a target id).
 *
 * The 403 envelope still collapses the page to a Forbidden state — we
 * never probe the role client-side. ADR-018 §2 + §5 pin the legal
 * moves; the per-row gating mirrors those rules and an attempt that
 * the backend rejects with 409 surfaces inline via the same
 * ``.notice danger`` class the load-error banner uses.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ApiError,
  createTaxonomyDraft,
  listTaxonomyVersions,
  synthesizeTaxonomy,
  transitionTaxonomyConcept,
  transitionTaxonomyVersion,
} from "../../api/client";
import type {
  ApiConceptSuggestion,
  ApiConceptSuggestionState,
  ApiTaxonomyState,
  ApiTaxonomyVersion,
} from "../../api/types";
import { ModalShell } from "./ModalShell";

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

/** ``2026-05-16T12:34:56Z`` → ``2026-05-16 12:34 UTC``. */
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

function countCategories(
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

// ─── State-machine gating ─────────────────────────────────────────────────

/** ADR-018 §2 — legal version transitions. Used both to disable
 *  buttons + to render the tooltip explaining why a button is greyed.
 *  Synthesize is a separate concern (PR #477) and lives below. */
function canTransitionVersionTo(
  state: ApiTaxonomyState,
  to: ApiTaxonomyState,
): boolean {
  switch (state) {
    case "DRAFT":
      return to === "CANDIDATE_V0" || to === "DISCARDED";
    case "CANDIDATE_V0":
      return to === "VALIDATED_V1" || to === "DISCARDED";
    case "VALIDATED_V1":
      return to === "ARCHIVED";
    case "ARCHIVED":
    case "DISCARDED":
      return false;
  }
}

const VERSION_DISABLED_REASON: Record<ApiTaxonomyState, string> = {
  DRAFT: "Only DRAFT → CANDIDATE or DRAFT → DISCARDED are legal here.",
  CANDIDATE_V0:
    "Only CANDIDATE → VALIDATED or CANDIDATE → DISCARDED are legal here.",
  VALIDATED_V1: "Only VALIDATED → ARCHIVED is legal here.",
  ARCHIVED: "Archived versions are terminal — no further transitions.",
  DISCARDED: "Discarded versions are terminal — no further transitions.",
};

/** ADR-018 §5 — legal concept-suggestion transitions. */
function canTransitionConceptTo(
  state: ApiConceptSuggestionState,
  to: ApiConceptSuggestionState,
): boolean {
  switch (state) {
    case "NEW":
      return (
        to === "UNDER_REVIEW" ||
        to === "ACCEPTED" ||
        to === "REJECTED" ||
        to === "DEFERRED"
      );
    case "UNDER_REVIEW":
      return (
        to === "ACCEPTED" ||
        to === "REJECTED" ||
        to === "MERGED" ||
        to === "DEFERRED"
      );
    case "DEFERRED":
      return to === "UNDER_REVIEW";
    case "ACCEPTED":
    case "REJECTED":
    case "MERGED":
      return false;
  }
}

const CONCEPT_STATE_LABELS: Record<ApiConceptSuggestionState, string> = {
  NEW: "New",
  UNDER_REVIEW: "Under review",
  ACCEPTED: "Accepted",
  REJECTED: "Rejected",
  MERGED: "Merged",
  DEFERRED: "Deferred",
};

// ─── Validate modal (version_label optional) ──────────────────────────────

interface ValidateModalProps {
  taxonomyId: string;
  versionNumber: number;
  defaultLabel: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (label: string | null) => void;
}

function ValidateModal({
  taxonomyId,
  versionNumber,
  defaultLabel,
  busy,
  onClose,
  onSubmit,
}: ValidateModalProps) {
  const [label, setLabel] = useState(defaultLabel);
  return (
    <ModalShell
      title={`Validate v${versionNumber} → VALIDATED_V1`}
      onClose={onClose}
    >
      <form
        className="modal-body"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(label.trim().length > 0 ? label.trim() : null);
        }}
      >
        <p className="muted">
          Promoting <code>{taxonomyId}</code> v{versionNumber} to the active
          taxonomy. Optionally attach a display label that downstream consumers
          will see.
        </p>
        <label htmlFor="taxonomy-validate-label" className="muted">
          Version label (optional)
        </label>
        <input
          id="taxonomy-validate-label"
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. 2026-Q2 launch"
          data-testid="taxonomy-validate-label"
        />
        <div className="modal-actions">
          <button
            type="button"
            className="text-button"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="primary-button"
            disabled={busy}
            data-testid="taxonomy-validate-submit"
          >
            {busy ? "Validating…" : "Validate"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

// ─── Create-draft modal ───────────────────────────────────────────────────

interface CreateDraftModalProps {
  busy: boolean;
  onClose: () => void;
  onSubmit: (body: {
    taxonomy_id?: string;
    source_version_number?: number;
  }) => void;
}

function CreateDraftModal({ busy, onClose, onSubmit }: CreateDraftModalProps) {
  const [taxonomyId, setTaxonomyId] = useState("");
  const [sourceVersion, setSourceVersion] = useState("");
  return (
    <ModalShell title="Create new taxonomy draft" onClose={onClose}>
      <form
        className="modal-body"
        onSubmit={(e) => {
          e.preventDefault();
          const body: { taxonomy_id?: string; source_version_number?: number } =
            {};
          const tid = taxonomyId.trim();
          if (tid.length > 0) body.taxonomy_id = tid;
          const sv = sourceVersion.trim();
          if (sv.length > 0) {
            const parsed = Number.parseInt(sv, 10);
            if (Number.isFinite(parsed)) body.source_version_number = parsed;
          }
          onSubmit(body);
        }}
      >
        <p className="muted">
          Empty fields mint a fresh lineage with an empty tree. Provide a
          <code> taxonomy_id</code> to add a version to an existing lineage, and
          a source version number to inherit its tree.
        </p>
        <label htmlFor="taxonomy-draft-id" className="muted">
          Taxonomy ID (optional)
        </label>
        <input
          id="taxonomy-draft-id"
          type="text"
          value={taxonomyId}
          onChange={(e) => setTaxonomyId(e.target.value)}
          placeholder="leave empty to mint a fresh lineage"
          data-testid="taxonomy-draft-id"
        />
        <label htmlFor="taxonomy-draft-source" className="muted">
          Source version number (optional)
        </label>
        <input
          id="taxonomy-draft-source"
          type="text"
          inputMode="numeric"
          value={sourceVersion}
          onChange={(e) => setSourceVersion(e.target.value)}
          placeholder="e.g. 2"
          data-testid="taxonomy-draft-source"
        />
        <div className="modal-actions">
          <button
            type="button"
            className="text-button"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="primary-button"
            disabled={busy}
            data-testid="taxonomy-draft-submit"
          >
            {busy ? "Creating…" : "Create draft"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

// ─── Merge concept modal ──────────────────────────────────────────────────

interface MergeConceptModalProps {
  conceptLabel: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (mergeTargetId: string) => void;
}

function MergeConceptModal({
  conceptLabel,
  busy,
  onClose,
  onSubmit,
}: MergeConceptModalProps) {
  const [targetId, setTargetId] = useState("");
  const submit = () => {
    const t = targetId.trim();
    if (t.length === 0) return;
    onSubmit(t);
  };
  return (
    <ModalShell title={`Merge "${conceptLabel}"`} onClose={onClose}>
      <form
        className="modal-body"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <p className="muted">
          Fold this suggestion into an existing category id from the
          version&apos;s tree. The merge target is required (ADR-018 §5).
        </p>
        <label htmlFor="taxonomy-merge-target" className="muted">
          Merge target category id
        </label>
        <input
          id="taxonomy-merge-target"
          type="text"
          value={targetId}
          onChange={(e) => setTargetId(e.target.value)}
          placeholder="e.g. battery.thermal"
          data-testid="taxonomy-merge-target"
        />
        <div className="modal-actions">
          <button
            type="button"
            className="text-button"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="primary-button"
            disabled={busy || targetId.trim().length === 0}
            data-testid="taxonomy-merge-submit"
          >
            {busy ? "Merging…" : "Merge"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

// ─── Main view ──────────────────────────────────────────────────────────────

export function AdminTaxonomyView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialId = searchParams.get("taxonomy_id") ?? "";

  const [pendingId, setPendingId] = useState(initialId);
  const [appliedId, setAppliedId] = useState(initialId);

  const [versions, setVersions] = useState<ApiTaxonomyVersion[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<ApiError | string | null>(null);

  // ADR-018 §2 mutations. Track the active row + busy state so the
  // operator can't double-fire transitions, and surface 409 / 400 /
  // 503 envelopes inline.
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<ApiError | string | null>(
    null,
  );
  const [expandedVersions, setExpandedVersions] = useState<Set<number>>(
    () => new Set(),
  );
  const [validateModal, setValidateModal] = useState<{
    versionNumber: number;
    defaultLabel: string;
  } | null>(null);
  const [createDraftOpen, setCreateDraftOpen] = useState(false);
  const [mergeModal, setMergeModal] = useState<{
    versionNumber: number;
    suggestion: ApiConceptSuggestion;
  } | null>(null);

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

  // Generic transition dispatcher. ``actionKey`` is logged into busy
  // state so the row's button shows the spinner instead of every
  // button in the table.
  const runVersionTransition = useCallback(
    async (
      versionNumber: number,
      to: ApiTaxonomyState,
      extras: { version_label?: string | null; reason?: string | null } = {},
    ) => {
      if (!appliedId) return;
      const actionKey = `v${versionNumber}:${to}`;
      setActionBusy(actionKey);
      setActionError(null);
      try {
        await transitionTaxonomyVersion(appliedId, versionNumber, {
          to_state: to,
          ...extras,
        });
        await loadVersions(appliedId);
      } catch (err: unknown) {
        if (err instanceof ApiError) setActionError(err);
        else if (err instanceof Error) setActionError(err.message);
        else setActionError("Transition failed.");
      } finally {
        setActionBusy(null);
      }
    },
    [appliedId, loadVersions],
  );

  const runSynthesize = useCallback(
    async (versionNumber: number) => {
      if (!appliedId) return;
      const actionKey = `v${versionNumber}:SYNTHESIZE`;
      setActionBusy(actionKey);
      setActionError(null);
      try {
        await synthesizeTaxonomy(appliedId, versionNumber);
        await loadVersions(appliedId);
      } catch (err: unknown) {
        if (err instanceof ApiError) setActionError(err);
        else if (err instanceof Error) setActionError(err.message);
        else setActionError("Synthesis failed.");
      } finally {
        setActionBusy(null);
      }
    },
    [appliedId, loadVersions],
  );

  const runConceptTransition = useCallback(
    async (
      versionNumber: number,
      suggestionId: string,
      to: ApiConceptSuggestionState,
      extras: { merge_target_id?: string | null; reason?: string | null } = {},
    ) => {
      if (!appliedId) return;
      const actionKey = `c${versionNumber}:${suggestionId}:${to}`;
      setActionBusy(actionKey);
      setActionError(null);
      try {
        await transitionTaxonomyConcept(
          appliedId,
          versionNumber,
          suggestionId,
          {
            to_state: to,
            ...extras,
          },
        );
        await loadVersions(appliedId);
      } catch (err: unknown) {
        if (err instanceof ApiError) setActionError(err);
        else if (err instanceof Error) setActionError(err.message);
        else setActionError("Concept transition failed.");
      } finally {
        setActionBusy(null);
      }
    },
    [appliedId, loadVersions],
  );

  const handleCreateDraft = useCallback(
    async (body: { taxonomy_id?: string; source_version_number?: number }) => {
      setActionBusy("draft:create");
      setActionError(null);
      try {
        const created = await createTaxonomyDraft(body);
        setCreateDraftOpen(false);
        // Jump the table over to the newly-created lineage so the
        // operator sees their work immediately.
        setPendingId(created.taxonomy_id);
        setAppliedId(created.taxonomy_id);
        setSearchParams({ taxonomy_id: created.taxonomy_id });
      } catch (err: unknown) {
        if (err instanceof ApiError) setActionError(err);
        else if (err instanceof Error) setActionError(err.message);
        else setActionError("Draft creation failed.");
      } finally {
        setActionBusy(null);
      }
    },
    [setSearchParams],
  );

  const toggleExpanded = useCallback((versionNumber: number) => {
    setExpandedVersions((prev) => {
      const next = new Set(prev);
      if (next.has(versionNumber)) next.delete(versionNumber);
      else next.add(versionNumber);
      return next;
    });
  }, []);

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
          <div className="action-row">
            <button
              type="button"
              className="primary-button"
              onClick={() => setCreateDraftOpen(true)}
              data-testid="taxonomy-create-draft"
              disabled={actionBusy === "draft:create"}
            >
              Create draft
            </button>
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

        {actionError !== null && (
          <div
            className="notice danger"
            role="alert"
            data-testid="taxonomy-action-error"
          >
            <strong>
              {actionError instanceof ApiError &&
              actionError.code === "KW_LLM_DISABLED"
                ? "LLM disabled — synthesize unavailable."
                : "Transition failed."}
            </strong>
            <span>
              {actionError instanceof ApiError
                ? actionError.detail
                : actionError}
            </span>
            {actionError instanceof ApiError && actionError.remediation && (
              <span className="muted">{actionError.remediation}</span>
            )}
            <button
              type="button"
              className="text-button"
              onClick={() => setActionError(null)}
              aria-label="Dismiss action error"
            >
              Dismiss
            </button>
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
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <VersionRowGroup
                  key={v.version_number}
                  version={v}
                  isActive={v.version_number === activeVersionNumber}
                  isExpanded={expandedVersions.has(v.version_number)}
                  busyKey={actionBusy}
                  onToggleExpand={() => toggleExpanded(v.version_number)}
                  onPromote={() =>
                    void runVersionTransition(v.version_number, "CANDIDATE_V0")
                  }
                  onValidate={() =>
                    setValidateModal({
                      versionNumber: v.version_number,
                      defaultLabel: v.version_label ?? "",
                    })
                  }
                  onArchive={() =>
                    void runVersionTransition(v.version_number, "ARCHIVED")
                  }
                  onDiscard={() =>
                    void runVersionTransition(v.version_number, "DISCARDED")
                  }
                  onSynthesize={() => void runSynthesize(v.version_number)}
                  onAcceptConcept={(s) =>
                    void runConceptTransition(
                      v.version_number,
                      s.suggestion_id,
                      "ACCEPTED",
                    )
                  }
                  onRejectConcept={(s) =>
                    void runConceptTransition(
                      v.version_number,
                      s.suggestion_id,
                      "REJECTED",
                    )
                  }
                  onDeferConcept={(s) =>
                    void runConceptTransition(
                      v.version_number,
                      s.suggestion_id,
                      "DEFERRED",
                    )
                  }
                  onMergeConcept={(s) =>
                    setMergeModal({
                      versionNumber: v.version_number,
                      suggestion: s,
                    })
                  }
                />
              ))}
            </tbody>
          </table>
        )}
      </section>

      {validateModal !== null && (
        <ValidateModal
          taxonomyId={appliedId}
          versionNumber={validateModal.versionNumber}
          defaultLabel={validateModal.defaultLabel}
          busy={actionBusy === `v${validateModal.versionNumber}:VALIDATED_V1`}
          onClose={() => setValidateModal(null)}
          onSubmit={(label) => {
            const vn = validateModal.versionNumber;
            setValidateModal(null);
            void runVersionTransition(vn, "VALIDATED_V1", {
              version_label: label,
            });
          }}
        />
      )}

      {createDraftOpen && (
        <CreateDraftModal
          busy={actionBusy === "draft:create"}
          onClose={() => setCreateDraftOpen(false)}
          onSubmit={(body) => void handleCreateDraft(body)}
        />
      )}

      {mergeModal !== null && (
        <MergeConceptModal
          conceptLabel={mergeModal.suggestion.label}
          busy={
            actionBusy ===
            `c${mergeModal.versionNumber}:${mergeModal.suggestion.suggestion_id}:MERGED`
          }
          onClose={() => setMergeModal(null)}
          onSubmit={(target) => {
            const vn = mergeModal.versionNumber;
            const sid = mergeModal.suggestion.suggestion_id;
            setMergeModal(null);
            void runConceptTransition(vn, sid, "MERGED", {
              merge_target_id: target,
            });
          }}
        />
      )}
    </main>
  );
}

// ─── Per-version row (table TR group: row + optional concepts panel) ──────

interface VersionRowGroupProps {
  version: ApiTaxonomyVersion;
  isActive: boolean;
  isExpanded: boolean;
  busyKey: string | null;
  onToggleExpand: () => void;
  onPromote: () => void;
  onValidate: () => void;
  onArchive: () => void;
  onDiscard: () => void;
  onSynthesize: () => void;
  onAcceptConcept: (s: ApiConceptSuggestion) => void;
  onRejectConcept: (s: ApiConceptSuggestion) => void;
  onDeferConcept: (s: ApiConceptSuggestion) => void;
  onMergeConcept: (s: ApiConceptSuggestion) => void;
}

function VersionRowGroup({
  version: v,
  isActive,
  isExpanded,
  busyKey,
  onToggleExpand,
  onPromote,
  onValidate,
  onArchive,
  onDiscard,
  onSynthesize,
  onAcceptConcept,
  onRejectConcept,
  onDeferConcept,
  onMergeConcept,
}: VersionRowGroupProps) {
  const reason = VERSION_DISABLED_REASON[v.state];
  const inflight = (state: ApiTaxonomyState) =>
    busyKey === `v${v.version_number}:${state}`;
  const anyBusy = busyKey !== null;
  return (
    <>
      <tr
        className={isActive ? "taxonomy-lineage-row-active" : undefined}
        data-testid={`taxonomy-lineage-row-${v.version_number}`}
        aria-current={isActive ? "true" : undefined}
      >
        <td className="lineage-version-number">v{v.version_number}</td>
        <td>
          <StatePill state={v.state} />
        </td>
        <td>{v.version_label ?? <span className="muted">—</span>}</td>
        <td>{countCategories(v.taxonomy.categories)}</td>
        <td>
          {v.suggestions.length > 0 ? (
            <button
              type="button"
              className="text-button"
              onClick={onToggleExpand}
              data-testid={`taxonomy-concepts-toggle-${v.version_number}`}
              aria-expanded={isExpanded}
            >
              {v.suggestions.length} {isExpanded ? "▾" : "▸"}
            </button>
          ) : (
            v.suggestions.length
          )}
        </td>
        <td>{v.created_by ?? <span className="muted">—</span>}</td>
        <td title={v.state_changed_at}>
          {formatTimestamp(v.state_changed_at)}
        </td>
        <td className="taxonomy-actions">
          <button
            type="button"
            className="text-button"
            disabled={
              !canTransitionVersionTo(v.state, "CANDIDATE_V0") || anyBusy
            }
            onClick={onPromote}
            title={
              canTransitionVersionTo(v.state, "CANDIDATE_V0")
                ? undefined
                : reason
            }
            data-testid={`taxonomy-promote-${v.version_number}`}
          >
            {inflight("CANDIDATE_V0") ? "Promoting…" : "Promote"}
          </button>
          <button
            type="button"
            className="text-button"
            disabled={
              !canTransitionVersionTo(v.state, "VALIDATED_V1") || anyBusy
            }
            onClick={onValidate}
            title={
              canTransitionVersionTo(v.state, "VALIDATED_V1")
                ? undefined
                : reason
            }
            data-testid={`taxonomy-validate-${v.version_number}`}
          >
            {inflight("VALIDATED_V1") ? "Validating…" : "Validate"}
          </button>
          <button
            type="button"
            className="text-button"
            disabled={!canTransitionVersionTo(v.state, "ARCHIVED") || anyBusy}
            onClick={onArchive}
            title={
              canTransitionVersionTo(v.state, "ARCHIVED") ? undefined : reason
            }
            data-testid={`taxonomy-archive-${v.version_number}`}
          >
            {inflight("ARCHIVED") ? "Archiving…" : "Archive"}
          </button>
          <button
            type="button"
            className="text-button"
            disabled={!canTransitionVersionTo(v.state, "DISCARDED") || anyBusy}
            onClick={onDiscard}
            title={
              canTransitionVersionTo(v.state, "DISCARDED") ? undefined : reason
            }
            data-testid={`taxonomy-discard-${v.version_number}`}
          >
            {inflight("DISCARDED") ? "Discarding…" : "Discard"}
          </button>
          {/* Synthesize is DRAFT-only — the route 409s on any other
              state, and the creator silently no-ops without any
              ACCEPTED/MERGED suggestions, so a fresh DRAFT with
              nothing accepted is the path of least surprise. The
              backend (#477) is the source of truth for the gates;
              the disabled checks here mirror it for UX clarity. */}
          <button
            type="button"
            className="text-button"
            disabled={v.state !== "DRAFT" || anyBusy}
            onClick={onSynthesize}
            title={
              v.state === "DRAFT"
                ? "Run the LLM over accepted suggestions and write the tree back onto this draft."
                : "Synthesize only runs on DRAFT versions."
            }
            data-testid={`taxonomy-synthesize-${v.version_number}`}
          >
            {busyKey === `v${v.version_number}:SYNTHESIZE`
              ? "Synthesizing…"
              : "Synthesize"}
          </button>
        </td>
      </tr>
      {isExpanded && v.suggestions.length > 0 && (
        <tr
          className="taxonomy-concepts-row"
          data-testid={`taxonomy-concepts-panel-${v.version_number}`}
        >
          <td colSpan={8}>
            <ConceptsTable
              versionNumber={v.version_number}
              suggestions={v.suggestions}
              busyKey={busyKey}
              onAccept={onAcceptConcept}
              onReject={onRejectConcept}
              onDefer={onDeferConcept}
              onMerge={onMergeConcept}
            />
          </td>
        </tr>
      )}
    </>
  );
}

// ─── Concepts sub-table ────────────────────────────────────────────────────

interface ConceptsTableProps {
  versionNumber: number;
  suggestions: ApiConceptSuggestion[];
  busyKey: string | null;
  onAccept: (s: ApiConceptSuggestion) => void;
  onReject: (s: ApiConceptSuggestion) => void;
  onDefer: (s: ApiConceptSuggestion) => void;
  onMerge: (s: ApiConceptSuggestion) => void;
}

function ConceptsTable({
  versionNumber,
  suggestions,
  busyKey,
  onAccept,
  onReject,
  onDefer,
  onMerge,
}: ConceptsTableProps) {
  const conceptInflight = (s: ApiConceptSuggestion, to: string) =>
    busyKey === `c${versionNumber}:${s.suggestion_id}:${to}`;
  const anyBusy = busyKey !== null;
  return (
    <table className="admin-table taxonomy-concepts-table">
      <thead>
        <tr>
          <th scope="col">Label</th>
          <th scope="col">State</th>
          <th scope="col">Description</th>
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        {suggestions.map((s) => (
          <tr
            key={s.suggestion_id}
            data-testid={`taxonomy-concept-row-${versionNumber}-${s.suggestion_id}`}
          >
            <td>{s.label}</td>
            <td>{CONCEPT_STATE_LABELS[s.state]}</td>
            <td className="muted">{s.description ?? "—"}</td>
            <td className="taxonomy-actions">
              <button
                type="button"
                className="text-button"
                disabled={
                  !canTransitionConceptTo(s.state, "ACCEPTED") || anyBusy
                }
                onClick={() => onAccept(s)}
                data-testid={`taxonomy-concept-accept-${s.suggestion_id}`}
              >
                {conceptInflight(s, "ACCEPTED") ? "Accepting…" : "Accept"}
              </button>
              <button
                type="button"
                className="text-button"
                disabled={
                  !canTransitionConceptTo(s.state, "REJECTED") || anyBusy
                }
                onClick={() => onReject(s)}
                data-testid={`taxonomy-concept-reject-${s.suggestion_id}`}
              >
                {conceptInflight(s, "REJECTED") ? "Rejecting…" : "Reject"}
              </button>
              <button
                type="button"
                className="text-button"
                disabled={
                  !canTransitionConceptTo(s.state, "DEFERRED") || anyBusy
                }
                onClick={() => onDefer(s)}
                data-testid={`taxonomy-concept-defer-${s.suggestion_id}`}
              >
                {conceptInflight(s, "DEFERRED") ? "Deferring…" : "Defer"}
              </button>
              <button
                type="button"
                className="text-button"
                disabled={!canTransitionConceptTo(s.state, "MERGED") || anyBusy}
                onClick={() => onMerge(s)}
                data-testid={`taxonomy-concept-merge-${s.suggestion_id}`}
              >
                {conceptInflight(s, "MERGED") ? "Merging…" : "Merge into…"}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
