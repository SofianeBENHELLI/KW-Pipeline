/**
 * Semantic-generation method options for the FSM action surface.
 *
 * The list is deliberately hardcoded — the backend exposes the live
 * registry via the route's 400 path on an unknown id, so the
 * frontend's worst case is a clean error banner rather than a
 * silently-mismatched dropdown. Operators add new methods by
 * extending this list (and the backend registry); there is no
 * dynamic discovery yet.
 *
 * The dropdown sits next to the "Semantic" FSM button — see
 * :class:`FsmActions`. The first non-disabled entry MUST be the
 * runtime default ("structure_first" / Method 1) per the 2026-05-14
 * product decision so the menu opens on the cheapest, most
 * predictable generator.
 *
 * Disabled entries stay visible (so the operator can see what's on
 * the runway) but the dropdown renders them as ``<option disabled>``
 * and the parent threads them past selection. Method 3 is disabled
 * today: the gen-time entity / claim / requirement / relationship
 * agents from the spec (#453) are not wired yet, and the current
 * backend generator only produces the widened type list without the
 * full agent fan-out — UX would be misleading.
 */

export interface SemanticMethodOption {
  /** Id sent to ``POST /semantic?method=…`` (must match a backend key). */
  id: string;
  /** Short label shown in the dropdown. */
  label: string;
  /** Helper text shown beneath the dropdown / in a title tooltip. */
  hint: string;
  /**
   * When true, the dropdown renders the option but refuses
   * selection. The label is suffixed with "(under development)" and
   * the hint surfaces the longer reason.
   */
  disabled?: boolean;
}

export const SEMANTIC_METHOD_OPTIONS: readonly SemanticMethodOption[] = [
  {
    id: "structure_first",
    label: "Method 1 — Structure-first (rule-based)",
    hint: "Parser sections + regex enrichers (dates / monetary / requirement cues + optional spaCy NER). Fastest, no LLM cost, stable across re-runs. Recommended default for high-volume ingestion.",
  },
  {
    id: "semantic_intelligence",
    label: "Method 2 — Semantic Document Intelligence (LLM)",
    hint: "One structured-output LLM call infers profile + typed assets (requirement / decision / risk / action_item / metric / definition / reference) with section-grounded citations. Requires an LLM provider key.",
  },
  {
    id: "knowledge_graph",
    label: "Method 3 — Knowledge Graph Extraction (LLM)",
    hint: "Under development — the gen-time entity / claim / requirement / relationship agents from the spec (see #453) are not wired yet. Method 3 will activate once those agents run at semantic-gen time instead of only as post-validation projector side-effects.",
    disabled: true,
  },
] as const;

// Product decision 2026-05-14: default opens on Method 1 so the
// dropdown lands on the cheapest, most predictable generator.
// Method 2 / Method 3 are opt-in via the dropdown.
export const DEFAULT_SEMANTIC_METHOD_ID = SEMANTIC_METHOD_OPTIONS[0].id;

/** Suffix appended to disabled option labels so the dropdown
 * communicates the state without relying on the disabled style
 * alone. */
export const UNDER_DEVELOPMENT_SUFFIX = " — under development";
