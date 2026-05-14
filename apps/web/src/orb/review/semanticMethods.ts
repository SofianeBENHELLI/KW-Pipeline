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
 * :class:`FsmActions`. The first entry MUST be the deployment
 * default ("deterministic") so the UX matches the unselected /
 * legacy posture.
 */

export interface SemanticMethodOption {
  /** Id sent to ``POST /semantic?method=…`` (must match a backend key). */
  id: string;
  /** Short label shown in the dropdown. */
  label: string;
  /** Helper text shown beneath the dropdown / in a title tooltip. */
  hint: string;
}

export const SEMANTIC_METHOD_OPTIONS: readonly SemanticMethodOption[] = [
  {
    id: "deterministic",
    label: "Deterministic (rule-based)",
    hint: "Parser sections + regex enrichers. Fast, no LLM cost, stable across re-runs.",
  },
  {
    id: "llm",
    label: "LLM extraction (instructor)",
    hint: "One structured-output LLM call infers profile + typed assets with section-grounded citations. Requires an LLM provider key.",
  },
] as const;

export const DEFAULT_SEMANTIC_METHOD_ID = SEMANTIC_METHOD_OPTIONS[0].id;
