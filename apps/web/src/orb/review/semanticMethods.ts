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
 * default ("structure_first" / Method 1) so the dropdown opens on
 * the cheapest, most predictable generator per the 2026-05-14
 * product decision.
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
    hint: "Same LLM call shape as Method 2 but with the widened taxonomy (claim / requirement / decision / action / risk / issue / kpi / definition / assumption / dependency / business_value / technical_capability / open_question) tuned for graph projection. Requires an LLM provider key.",
  },
] as const;

// Product decision 2026-05-14: default opens on Method 1 so the
// dropdown lands on the cheapest, most predictable generator.
// Method 2 / Method 3 are opt-in via the dropdown.
export const DEFAULT_SEMANTIC_METHOD_ID = SEMANTIC_METHOD_OPTIONS[0].id;
