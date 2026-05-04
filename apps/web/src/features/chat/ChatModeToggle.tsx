/**
 * Mode toggle for ``<ChatPanel/>`` — switches between RAG, GraphRAG,
 * and Hybrid retrieval modes.
 *
 * Pure presentation: the parent owns the mode state and decides what
 * to do when the user picks a different option. Rendered as a tablist
 * with three buttons so screen readers announce the change.
 */

import type { ApiChatMode } from "../../api/types";

const OPTIONS: { id: ApiChatMode; label: string; hint: string }[] = [
  { id: "rag", label: "RAG", hint: "Vector search over chunk excerpts." },
  {
    id: "graph",
    label: "GraphRAG",
    hint: "Projected entity triples from the knowledge graph.",
  },
  {
    id: "hybrid",
    label: "Hybrid",
    hint: "Both chunk excerpts and entity triples.",
  },
];

export interface ChatModeToggleProps {
  value: ApiChatMode;
  onChange: (next: ApiChatMode) => void;
  /** When true, all options render disabled (e.g. while a request is in-flight). */
  disabled?: boolean;
}

export function ChatModeToggle({ value, onChange, disabled = false }: ChatModeToggleProps) {
  return (
    <div
      className="chat-panel__mode"
      role="tablist"
      aria-label="Chat retrieval mode"
      data-testid="chat-mode-toggle"
    >
      {OPTIONS.map((option) => {
        const isActive = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            role="tab"
            className={
              isActive
                ? "chat-panel__mode-btn chat-panel__mode-btn--active"
                : "chat-panel__mode-btn"
            }
            aria-selected={isActive}
            aria-label={`${option.label}: ${option.hint}`}
            disabled={disabled}
            onClick={() => onChange(option.id)}
            data-testid={`chat-mode-${option.id}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
