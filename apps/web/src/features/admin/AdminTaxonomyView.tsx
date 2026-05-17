/**
 * Admin UI — Taxonomy versioning lifecycle view (EPIC-1 §1.9, ADR-018).
 *
 * Operator surface over the ``/admin/taxonomy/versions/{taxonomy_id}``
 * lineage. The view exposes the lifecycle state of every version in a
 * taxonomy family as a lineage panel:
 *
 *     Draft 1 → Candidate V0 → Validated V1 → Archived
 *
 * Each row shows the version's state badge, label, audit metadata
 * (created_by, state_changed_at), suggestion / category counts, and a
 * per-state action cell driving the ADR-018 §2 state machine.
 *
 * Slice 1.9 read-only view shipped in #479; this slice ships the
 * operator actions deferred there:
 *
 * - **Per-row transition buttons** — Promote / Validate / Archive /
 *   Discard / Synthesize per the legal moves table in ADR-018 §2.
 * - **Validate modal** — collects the optional ``version_label`` the
 *   transition record pins on the version.
 * - **Archive / Discard modals** — collect the optional ``reason`` that
 *   lands on the audit event.
 * - **Create draft button** — opens a modal mirroring the three
 *   ``CreateDraftRequest`` modes (fresh / branch / inherit).
 * - **Concepts sub-table** — per-suggestion FSM actions (accept /
 *   reject / defer / under-review / merge-into).
 *
 * Refetch fires after every mutation so the table reflects the new
 * state without operator intervention. 409 illegal-transition /
 * 400 missing-merge-target / 503 ``KW_LLM_DISABLED`` envelopes surface
 * inline via the shared ``.notice danger`` banner.
 *
 * Auth posture follows the rest of ``/admin/*`` — the API gates with
 * ``require_admin``; the UI doesn't probe role client-side and a 403
 * envelope collapses the page to a "Forbidden" state.
 */

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
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

// ─── Concept-suggestion badge ───────────────────────────────────────────────

const SUGGESTION_LABELS: Record<ApiConceptSuggestionState, string> = {
  NEW: "New",
  UNDER_REVIEW: "Under review",
  ACCEPTED: "Accepted",
  REJECTED: "Rejected",
  MERGED: "Merged",
  DEFERRED: "Deferred",
};

const SUGGESTION_MODIFIERS: Record<ApiConceptSuggestionState, string> = {
  NEW: "state-pill--candidate",
  UNDER_REVIEW: "state-pill--draft",
  ACCEPTED: "state-pill--validated",
  REJECTED: "state-pill--discarded",
  MERGED: "state-pill--archived",
  DEFERRED: "state-pill--archived",
};

interface SuggestionStatePillProps {
  state: ApiConceptSuggestionState;
}

export function SuggestionStatePill({ state }: SuggestionStatePillProps) {
  return (
    <span
      className={`state-pill ${SUGGESTION_MODIFIERS[state]}`}
      data-testid={`suggestion-state-pill-${state}`}
    >
      {SUGGESTION_LABELS[state]}
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

// ─── Action-cell helpers ────────────────────────────────────────────────────

/** Distil an API failure into a single human-readable string for the
 *  shared error banner. ``ApiError`` carries the envelope's ``detail``
 *  verbatim — including the canonical illegal-transition message — so
 *  surfacing it without wrapping is the simplest thing. */
function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return fallback;
}

// ─── Modal: validate ────────────────────────────────────────────────────────

interface ValidateModalProps {
  version: ApiTaxonomyVersion;
  onClose: () => void;
  onCompleted: (next: ApiTaxonomyVersion) => void;
  onError: (message: string) => void;
}

function ValidateModal({
  version,
  onClose,
  onCompleted,
  onError,
}: ValidateModalProps) {
  const [versionLabel, setVersionLabel] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = useCallback(() => {
    setBusy(true);
    transitionTaxonomyVersion(version.taxonomy_id, version.version_number, {
      to_state: "VALIDATED_V1",
      version_label: versionLabel.trim() === "" ? null : versionLabel.trim(),
    })
      .then((next) => onCompleted(next))
      .catch((err: unknown) => {
        onError(errorMessage(err, "Validate failed."));
      })
      .finally(() => setBusy(false));
  }, [version, versionLabel, onCompleted, onError]);

  return (
    <ModalShell title="Promote to Validated V1?" onClose={onClose}>
      <p>
        Validate <code>v{version.version_number}</code> of{" "}
        <code>{version.taxonomy_id}</code>. The version_label below is pinned on
        the audit event and surfaced in the lineage table.
      </p>
      <div className="form-grid">
        <label>
          <span className="muted">Version label</span>
          <input
            type="text"
            value={versionLabel}
            onChange={(e) => setVersionLabel(e.target.value)}
            placeholder="e.g. 2026-Q1 launch"
            disabled={busy}
            data-testid="validate-version-label"
          />
        </label>
      </div>
      <div className="action-row">
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          onClick={handleSubmit}
          disabled={busy}
          aria-busy={busy}
          data-testid="validate-submit"
        >
          {busy ? "Validating…" : "Validate"}
        </button>
      </div>
    </ModalShell>
  );
}

// ─── Modal: archive / discard ───────────────────────────────────────────────

interface ReasonModalProps {
  version: ApiTaxonomyVersion;
  targetState: "ARCHIVED" | "DISCARDED";
  onClose: () => void;
  onCompleted: (next: ApiTaxonomyVersion) => void;
  onError: (message: string) => void;
}

function ReasonModal({
  version,
  targetState,
  onClose,
  onCompleted,
  onError,
}: ReasonModalProps) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const verbPresent = targetState === "ARCHIVED" ? "Archive" : "Discard";
  const verbProgressive =
    targetState === "ARCHIVED" ? "Archiving…" : "Discarding…";

  const handleSubmit = useCallback(() => {
    setBusy(true);
    transitionTaxonomyVersion(version.taxonomy_id, version.version_number, {
      to_state: targetState,
      reason: reason.trim() === "" ? null : reason.trim(),
    })
      .then((next) => onCompleted(next))
      .catch((err: unknown) => {
        onError(errorMessage(err, `${verbPresent} failed.`));
      })
      .finally(() => setBusy(false));
  }, [version, targetState, reason, onCompleted, onError, verbPresent]);

  return (
    <ModalShell title={`${verbPresent} version?`} onClose={onClose}>
      <p>
        {verbPresent} <code>v{version.version_number}</code> of{" "}
        <code>{version.taxonomy_id}</code>. The reason below lands on the audit
        event.
      </p>
      <div className="form-grid">
        <label>
          <span className="muted">Reason</span>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="optional — e.g. superseded by V2"
            disabled={busy}
            data-testid="reason-input"
          />
        </label>
      </div>
      <div className="action-row">
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="primary-button danger"
          onClick={handleSubmit}
          disabled={busy}
          aria-busy={busy}
          data-testid="reason-submit"
        >
          {busy ? verbProgressive : verbPresent}
        </button>
      </div>
    </ModalShell>
  );
}

// ─── Modal: create draft ────────────────────────────────────────────────────

interface CreateDraftModalProps {
  onClose: () => void;
  onCompleted: (next: ApiTaxonomyVersion) => void;
  onError: (message: string) => void;
}

function CreateDraftModal({
  onClose,
  onCompleted,
  onError,
}: CreateDraftModalProps) {
  const [taxonomyId, setTaxonomyId] = useState("");
  const [sourceVersionNumber, setSourceVersionNumber] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = useCallback(() => {
    setBusy(true);
    // Empty taxonomy_id → omit (server mints a fresh id). Empty source
    // version → omit (start with empty tree).
    const body: {
      taxonomy_id?: string;
      source_version_number?: number;
    } = {};
    const trimmedId = taxonomyId.trim();
    if (trimmedId !== "") body.taxonomy_id = trimmedId;
    const trimmedSrc = sourceVersionNumber.trim();
    if (trimmedSrc !== "") {
      const parsed = Number.parseInt(trimmedSrc, 10);
      if (!Number.isNaN(parsed)) body.source_version_number = parsed;
    }
    createTaxonomyDraft(body)
      .then((next) => onCompleted(next))
      .catch((err: unknown) => {
        onError(errorMessage(err, "Create draft failed."));
      })
      .finally(() => setBusy(false));
  }, [taxonomyId, sourceVersionNumber, onCompleted, onError]);

  return (
    <ModalShell title="Create new draft" onClose={onClose}>
      <p>
        Mint a new <code>DRAFT</code> taxonomy version. Leave both fields empty
        to mint a fresh lineage; set <code>taxonomy_id</code> alone to start a
        new empty version in that family; set both to branch from the named
        source.
      </p>
      <div className="form-grid">
        <label>
          <span className="muted">Taxonomy id</span>
          <input
            type="text"
            value={taxonomyId}
            onChange={(e) => setTaxonomyId(e.target.value)}
            placeholder="optional — leave empty to mint a fresh one"
            disabled={busy}
            data-testid="create-draft-taxonomy-id"
          />
        </label>
        <label>
          <span className="muted">Source version</span>
          <input
            type="number"
            value={sourceVersionNumber}
            onChange={(e) => setSourceVersionNumber(e.target.value)}
            placeholder="optional integer — inherits the tree"
            disabled={busy}
            min={1}
            step={1}
            data-testid="create-draft-source-version"
          />
        </label>
      </div>
      <div className="action-row">
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          onClick={handleSubmit}
          disabled={busy}
          aria-busy={busy}
          data-testid="create-draft-submit"
        >
          {busy ? "Creating…" : "Create draft"}
        </button>
      </div>
    </ModalShell>
  );
}

// ─── Modal: merge concept ──────────────────────────────────────────────────

interface MergeConceptModalProps {
  version: ApiTaxonomyVersion;
  suggestion: ApiConceptSuggestion;
  onClose: () => void;
  onCompleted: () => void;
  onError: (message: string) => void;
}

function MergeConceptModal({
  version,
  suggestion,
  onClose,
  onCompleted,
  onError,
}: MergeConceptModalProps) {
  const [mergeTargetId, setMergeTargetId] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = useCallback(() => {
    setBusy(true);
    const trimmed = mergeTargetId.trim();
    // Empty submission deliberately sends null so the server returns
    // its canonical 400 ``merge_target_id required`` envelope, which
    // the parent surfaces inline. Lets an operator see the validation
    // message without re-implementing it client-side.
    transitionTaxonomyConcept(
      version.taxonomy_id,
      version.version_number,
      suggestion.suggestion_id,
      {
        to_state: "MERGED",
        merge_target_id: trimmed === "" ? null : trimmed,
      },
    )
      .then(() => onCompleted())
      .catch((err: unknown) => {
        onError(errorMessage(err, "Merge failed."));
      })
      .finally(() => setBusy(false));
  }, [version, suggestion, mergeTargetId, onCompleted, onError]);

  return (
    <ModalShell title="Merge concept into…" onClose={onClose}>
      <p>
        Fold <strong>{suggestion.label}</strong> into an existing category. The
        merge target id is pinned on the audit event so the merge can be
        replayed later.
      </p>
      <div className="form-grid">
        <label>
          <span className="muted">Merge target id</span>
          <input
            type="text"
            value={mergeTargetId}
            onChange={(e) => setMergeTargetId(e.target.value)}
            placeholder="e.g. battery.thermal"
            disabled={busy}
            data-testid="merge-target-id"
          />
        </label>
      </div>
      <div className="action-row">
        <button
          type="button"
          className="secondary-button"
          onClick={onClose}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          onClick={handleSubmit}
          disabled={busy}
          aria-busy={busy}
          data-testid="merge-submit"
        >
          {busy ? "Merging…" : "Merge"}
        </button>
      </div>
    </ModalShell>
  );
}

// ─── Action cell — per-row transition buttons ──────────────────────────────

interface VersionActionsProps {
  version: ApiTaxonomyVersion;
  expanded: boolean;
  onToggleConcepts: () => void;
  onPromote: () => Promise<void>;
  onValidate: () => void;
  onArchive: () => void;
  onDiscard: () => void;
  onSynthesize: () => Promise<void>;
  busy: boolean;
}

function VersionActions({
  version,
  expanded,
  onToggleConcepts,
  onPromote,
  onValidate,
  onArchive,
  onDiscard,
  onSynthesize,
  busy,
}: VersionActionsProps) {
  // The legal-move table lives here so a future schema change touches
  // one place. Disabled buttons advertise WHY via ``title``.
  switch (version.state) {
    case "DRAFT":
      return (
        <div
          className="taxonomy-action-cell"
          data-testid="taxonomy-actions-draft"
        >
          <button
            type="button"
            className="secondary-button"
            onClick={() => void onPromote()}
            disabled={busy}
            data-testid="action-promote"
          >
            Promote → Candidate
          </button>
          <button
            type="button"
            className="secondary-button danger"
            onClick={onDiscard}
            disabled={busy}
            data-testid="action-discard"
          >
            Discard
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void onSynthesize()}
            disabled={busy}
            data-testid="action-synthesize"
          >
            Synthesize
          </button>
          <button
            type="button"
            className="text-button"
            onClick={onToggleConcepts}
            data-testid="action-toggle-concepts"
            aria-expanded={expanded}
          >
            {expanded
              ? `Hide concepts (${version.suggestions.length})`
              : `Concepts (${version.suggestions.length})`}
          </button>
        </div>
      );
    case "CANDIDATE_V0":
      return (
        <div
          className="taxonomy-action-cell"
          data-testid="taxonomy-actions-candidate"
        >
          <button
            type="button"
            className="primary-button"
            onClick={onValidate}
            disabled={busy}
            data-testid="action-validate"
          >
            Validate → V1
          </button>
          <button
            type="button"
            className="secondary-button danger"
            onClick={onDiscard}
            disabled={busy}
            data-testid="action-discard"
          >
            Discard
          </button>
        </div>
      );
    case "VALIDATED_V1":
      return (
        <div
          className="taxonomy-action-cell"
          data-testid="taxonomy-actions-validated"
        >
          <button
            type="button"
            className="secondary-button"
            onClick={onArchive}
            disabled={busy}
            data-testid="action-archive"
          >
            Archive
          </button>
        </div>
      );
    case "ARCHIVED":
    case "DISCARDED":
      // Terminal states. Nothing to do.
      return (
        <div
          className="taxonomy-action-cell muted"
          data-testid="taxonomy-actions-terminal"
        >
          —
        </div>
      );
  }
}

// ─── Concepts sub-table ────────────────────────────────────────────────────

interface ConceptsSubTableProps {
  version: ApiTaxonomyVersion;
  onAction: (
    suggestion: ApiConceptSuggestion,
    targetState: ApiConceptSuggestionState,
  ) => Promise<void>;
  onOpenMerge: (suggestion: ApiConceptSuggestion) => void;
  busy: boolean;
}

function ConceptsSubTable({
  version,
  onAction,
  onOpenMerge,
  busy,
}: ConceptsSubTableProps) {
  if (version.suggestions.length === 0) {
    return (
      <p className="muted" data-testid="concepts-empty">
        No concept suggestions for this draft.
      </p>
    );
  }
  return (
    <table
      className="admin-table concepts-sub-table"
      data-testid="concepts-sub-table"
    >
      <caption className="visually-hidden">
        Concept suggestions for v{version.version_number}
      </caption>
      <thead>
        <tr>
          <th scope="col">Label</th>
          <th scope="col">State</th>
          <th scope="col">Source</th>
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        {version.suggestions.map((s) => (
          <tr
            key={s.suggestion_id}
            data-testid={`concept-row-${s.suggestion_id}`}
          >
            <td>{s.label}</td>
            <td>
              <SuggestionStatePill state={s.state} />
            </td>
            <td>
              <code>{s.source}</code>
            </td>
            <td>
              <ConceptActions
                suggestion={s}
                onAction={onAction}
                onOpenMerge={onOpenMerge}
                busy={busy}
              />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface ConceptActionsProps {
  suggestion: ApiConceptSuggestion;
  onAction: (
    suggestion: ApiConceptSuggestion,
    targetState: ApiConceptSuggestionState,
  ) => Promise<void>;
  onOpenMerge: (suggestion: ApiConceptSuggestion) => void;
  busy: boolean;
}

function ConceptActions({
  suggestion,
  onAction,
  onOpenMerge,
  busy,
}: ConceptActionsProps) {
  switch (suggestion.state) {
    case "NEW":
    case "UNDER_REVIEW":
      return (
        <div className="taxonomy-action-cell">
          <button
            type="button"
            className="secondary-button"
            onClick={() => void onAction(suggestion, "ACCEPTED")}
            disabled={busy}
            data-testid={`concept-accept-${suggestion.suggestion_id}`}
          >
            Accept
          </button>
          <button
            type="button"
            className="secondary-button danger"
            onClick={() => void onAction(suggestion, "REJECTED")}
            disabled={busy}
            data-testid={`concept-reject-${suggestion.suggestion_id}`}
          >
            Reject
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void onAction(suggestion, "DEFERRED")}
            disabled={busy}
            data-testid={`concept-defer-${suggestion.suggestion_id}`}
          >
            Defer
          </button>
          {suggestion.state === "NEW" && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => void onAction(suggestion, "UNDER_REVIEW")}
              disabled={busy}
              data-testid={`concept-under-review-${suggestion.suggestion_id}`}
            >
              Under review
            </button>
          )}
          <button
            type="button"
            className="secondary-button"
            onClick={() => onOpenMerge(suggestion)}
            disabled={busy}
            data-testid={`concept-merge-${suggestion.suggestion_id}`}
          >
            Merge into…
          </button>
        </div>
      );
    case "DEFERRED":
      return (
        <div className="taxonomy-action-cell">
          <button
            type="button"
            className="secondary-button"
            onClick={() => void onAction(suggestion, "UNDER_REVIEW")}
            disabled={busy}
            data-testid={`concept-under-review-${suggestion.suggestion_id}`}
          >
            Under review
          </button>
        </div>
      );
    case "ACCEPTED":
    case "REJECTED":
    case "MERGED":
      return <span className="muted">—</span>;
  }
}

// ─── Main view ──────────────────────────────────────────────────────────────

type ModalState =
  | { kind: "none" }
  | { kind: "create-draft" }
  | { kind: "validate"; version: ApiTaxonomyVersion }
  | {
      kind: "reason";
      version: ApiTaxonomyVersion;
      targetState: "ARCHIVED" | "DISCARDED";
    }
  | {
      kind: "merge-concept";
      version: ApiTaxonomyVersion;
      suggestion: ApiConceptSuggestion;
    };

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

  // Per-action error banner (409 / 400 / 503 from a mutation). Separate
  // from ``loadError`` so a failed transition doesn't blow the table
  // away — the row stays visible and the operator retries.
  const [actionError, setActionError] = useState<string | null>(null);
  // Single in-flight flag for mutations — disables every action button
  // while a transition resolves so an operator can't double-fire.
  const [actionBusy, setActionBusy] = useState(false);
  const [modal, setModal] = useState<ModalState>({ kind: "none" });
  // Expanded concept rows. Set<version_number>.
  const [expandedConcepts, setExpandedConcepts] = useState<Set<number>>(
    new Set(),
  );

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
    setActionError(null);
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

  // ─── Mutation handlers ────────────────────────────────────────────────────

  const refetchAfterMutation = useCallback(
    async (taxonomyId: string) => {
      // Always re-load the lineage after a mutation so the table
      // reflects the new state even when the mutation returned a
      // single version (some downstream rows may also have moved).
      await loadVersions(taxonomyId);
    },
    [loadVersions],
  );

  const handlePromote = useCallback(
    async (version: ApiTaxonomyVersion) => {
      setActionBusy(true);
      setActionError(null);
      try {
        await transitionTaxonomyVersion(
          version.taxonomy_id,
          version.version_number,
          { to_state: "CANDIDATE_V0" },
        );
        await refetchAfterMutation(version.taxonomy_id);
      } catch (err: unknown) {
        setActionError(errorMessage(err, "Promote failed."));
      } finally {
        setActionBusy(false);
      }
    },
    [refetchAfterMutation],
  );

  const handleSynthesize = useCallback(
    async (version: ApiTaxonomyVersion) => {
      setActionBusy(true);
      setActionError(null);
      try {
        await synthesizeTaxonomy(version.taxonomy_id, version.version_number);
        await refetchAfterMutation(version.taxonomy_id);
      } catch (err: unknown) {
        setActionError(errorMessage(err, "Synthesize failed."));
      } finally {
        setActionBusy(false);
      }
    },
    [refetchAfterMutation],
  );

  const handleModalCompleted = useCallback(
    async (next: ApiTaxonomyVersion) => {
      setModal({ kind: "none" });
      setActionError(null);
      await refetchAfterMutation(next.taxonomy_id);
    },
    [refetchAfterMutation],
  );

  // The create-draft modal's completed payload may carry a fresh
  // taxonomy_id (the empty-body mode). Switch the applied id over so
  // the lineage table loads the new lineage.
  const handleCreateDraftCompleted = useCallback(
    async (next: ApiTaxonomyVersion) => {
      setModal({ kind: "none" });
      setActionError(null);
      setPendingId(next.taxonomy_id);
      setAppliedId(next.taxonomy_id);
      setSearchParams({ taxonomy_id: next.taxonomy_id });
    },
    [setSearchParams],
  );

  const handleConceptAction = useCallback(
    async (
      version: ApiTaxonomyVersion,
      suggestion: ApiConceptSuggestion,
      targetState: ApiConceptSuggestionState,
    ) => {
      setActionBusy(true);
      setActionError(null);
      try {
        await transitionTaxonomyConcept(
          version.taxonomy_id,
          version.version_number,
          suggestion.suggestion_id,
          { to_state: targetState },
        );
        await refetchAfterMutation(version.taxonomy_id);
      } catch (err: unknown) {
        setActionError(errorMessage(err, "Concept transition failed."));
      } finally {
        setActionBusy(false);
      }
    },
    [refetchAfterMutation],
  );

  const toggleConcepts = useCallback((versionNumber: number) => {
    setExpandedConcepts((prev) => {
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
              onClick={() => {
                setActionError(null);
                setModal({ kind: "create-draft" });
              }}
              data-testid="create-draft-button"
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
            <strong>Action failed.</strong>
            <span>{actionError}</span>
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
              {versions.map((v) => {
                const expanded = expandedConcepts.has(v.version_number);
                return (
                  <Fragment key={v.version_number}>
                    <tr
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
                      <td>
                        {v.version_label ?? <span className="muted">—</span>}
                      </td>
                      <td>{countCategories(v.taxonomy.categories)}</td>
                      <td>{v.suggestions.length}</td>
                      <td>
                        {v.created_by ?? <span className="muted">—</span>}
                      </td>
                      <td title={v.state_changed_at}>
                        {formatTimestamp(v.state_changed_at)}
                      </td>
                      <td>
                        <VersionActions
                          version={v}
                          expanded={expanded}
                          onToggleConcepts={() =>
                            toggleConcepts(v.version_number)
                          }
                          onPromote={() => handlePromote(v)}
                          onValidate={() =>
                            setModal({ kind: "validate", version: v })
                          }
                          onArchive={() =>
                            setModal({
                              kind: "reason",
                              version: v,
                              targetState: "ARCHIVED",
                            })
                          }
                          onDiscard={() =>
                            setModal({
                              kind: "reason",
                              version: v,
                              targetState: "DISCARDED",
                            })
                          }
                          onSynthesize={() => handleSynthesize(v)}
                          busy={actionBusy}
                        />
                      </td>
                    </tr>
                    {v.state === "DRAFT" && expanded && (
                      <tr data-testid={`concepts-row-${v.version_number}`}>
                        <td colSpan={8}>
                          <ConceptsSubTable
                            version={v}
                            onAction={(s, target) =>
                              handleConceptAction(v, s, target)
                            }
                            onOpenMerge={(s) =>
                              setModal({
                                kind: "merge-concept",
                                version: v,
                                suggestion: s,
                              })
                            }
                            busy={actionBusy}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {modal.kind === "create-draft" && (
        <CreateDraftModal
          onClose={() => setModal({ kind: "none" })}
          onCompleted={(next) => void handleCreateDraftCompleted(next)}
          onError={setActionError}
        />
      )}
      {modal.kind === "validate" && (
        <ValidateModal
          version={modal.version}
          onClose={() => setModal({ kind: "none" })}
          onCompleted={(next) => void handleModalCompleted(next)}
          onError={setActionError}
        />
      )}
      {modal.kind === "reason" && (
        <ReasonModal
          version={modal.version}
          targetState={modal.targetState}
          onClose={() => setModal({ kind: "none" })}
          onCompleted={(next) => void handleModalCompleted(next)}
          onError={setActionError}
        />
      )}
      {modal.kind === "merge-concept" && (
        <MergeConceptModal
          version={modal.version}
          suggestion={modal.suggestion}
          onClose={() => setModal({ kind: "none" })}
          onCompleted={() => {
            const tid = modal.version.taxonomy_id;
            setModal({ kind: "none" });
            setActionError(null);
            void refetchAfterMutation(tid);
          }}
          onError={setActionError}
        />
      )}
    </main>
  );
}
