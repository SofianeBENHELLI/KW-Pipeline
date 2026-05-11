import { useState } from "react";

import { ApiError, askKnowledgeChat } from "../api/client";
import type { ApiChatMode, ApiChatResponse } from "../api/types";
import { Btn, Card, Mono } from "../ui/orb";
import { Input } from "../ui/orb/atoms";

const MODES: { id: ApiChatMode; label: string }[] = [
  { id: "rag", label: "RAG" },
  { id: "graph", label: "GraphRAG" },
  { id: "hybrid", label: "Hybrid" },
];

export interface OrbChatPanelProps {
  onSelectCitation?: (documentId: string) => void;
}

/**
 * Phase-5 grounded chat panel — slide-out on the shell's right edge.
 * Wired to POST /knowledge/chat with the three retrieval modes (RAG /
 * GraphRAG / Hybrid). Renders the 503 KW_CHAT_DISABLED remediation
 * banner the same way SearchPanel does.
 */
export function OrbChatPanel({ onSelectCitation }: OrbChatPanelProps) {
  const [mode, setMode] = useState<ApiChatMode>("rag");
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [response, setResponse] = useState<ApiChatResponse | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    if (!question.trim() || pending) return;
    setPending(true);
    setError(null);
    setDisabled(false);
    try {
      const reply = await askKnowledgeChat(question.trim(), { mode });
      setResponse(reply);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setDisabled(true);
        setResponse(null);
      } else {
        const message =
          err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
        setError(message);
        setResponse(null);
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="orb-aside">
      <div className="orb-aside__head">
        <span className="orb-aside__title">Grounded chat</span>
        <span className="orb-aside__meta orb-mono">
          {response?.llm_model ? response.llm_model : ""}
        </span>
      </div>
      <div className="orb-aside__body">
        <div className="orb-aside__modes" role="tablist" aria-label="Retrieval mode">
          {MODES.map((option) => (
            <Btn
              key={option.id}
              kind={option.id === mode ? "primary" : "ghost"}
              size="xs"
              role="tab"
              aria-selected={option.id === mode}
              onClick={() => setMode(option.id)}
            >
              {option.label}
            </Btn>
          ))}
        </div>
        <Input
          placeholder="Ask a question grounded in the corpus…"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void ask();
            }
          }}
        />
        <Btn kind="primary" onClick={() => void ask()} disabled={pending || !question.trim()}>
          {pending ? "Thinking…" : "Ask"}
        </Btn>
        {disabled && (
          <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">
            Chat is disabled. Set <Mono>VOYAGE_API_KEY</Mono> and an LLM provider key on the backend.
          </div>
        )}
        {error && (
          <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">{error}</div>
        )}
        {response && (
          <Card className="orb-aside__answer">
            <p className="orb-aside__answer-body">{response.answer || "(no answer)"}</p>
            {response.citations.length > 0 && (
              <ul className="orb-aside__citations">
                {response.citations.map((citation) => (
                  <li key={citation.chunk_id}>
                    <button
                      type="button"
                      className="orb-aside__citation"
                      onClick={() => onSelectCitation?.(citation.document_id)}
                    >
                      <Mono>{citation.document_id.slice(0, 8)}</Mono>
                      <span> · {citation.snippet ?? "—"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}
