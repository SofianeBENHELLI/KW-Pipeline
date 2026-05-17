/**
 * ChatPanel — Knowledge Forge grounded chat surface (`/kf/chat`).
 *
 * Per design §6.2: mode toggle (RAG / GraphRAG / Hybrid; Hybrid
 * default), message stack, assistant messages with inline mono
 * citations `[1] [2] …`, composer with Enter-to-send + Shift-Enter
 * newline, token estimate on the right.
 *
 * Phase-3 gating: a 503 `KW_CHAT_DISABLED` envelope surfaces the
 * remediation message verbatim.
 */

import { useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import { Btn, OrbI } from "../index";
import "./search.css";
import { ApiError, askKnowledgeChat } from "../../api/client";
import type {
  ApiChatCitation,
  ApiChatMode,
  ApiChatResponse,
} from "../../api/types";

interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations?: ApiChatCitation[];
  tokenUsage?: ApiChatResponse["token_usage"];
  warnings?: string[];
}

export function ChatPanel(): ReactElement {
  const navigate = useNavigate();
  const [mode, setMode] = useState<ApiChatMode>("hybrid");
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [loading, setLoading] = useState(false);
  const [disabled, setDisabled] = useState<{
    message: string;
    remediation: string;
  } | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const send = async () => {
    const question = draft.trim();
    if (!question || loading) return;
    setError(null);
    setDisabled(null);
    const userTurn: ChatTurn = {
      id: `u-${Date.now()}`,
      role: "user",
      text: question,
    };
    setTurns((t) => [...t, userTurn]);
    setDraft("");
    setLoading(true);
    try {
      const resp = await askKnowledgeChat(question, { mode, top_k: 8 });
      const reply: ChatTurn = {
        id: `a-${Date.now()}`,
        role: "assistant",
        text: resp.answer,
        citations: resp.citations,
        tokenUsage: resp.token_usage,
        warnings: resp.warnings,
      };
      setTurns((t) => [...t, reply]);
    } catch (err) {
      if (err instanceof ApiError && err.code === "KW_CHAT_DISABLED") {
        setDisabled({
          message: err.detail ?? "Chat disabled",
          remediation:
            err.remediation ??
            "Set KW_KNOWLEDGE_LAYER_ENABLED=true and configure VOYAGE_API_KEY + an LLM key.",
        });
      } else {
        setError(err instanceof Error ? err : new Error(String(err)));
      }
    } finally {
      setLoading(false);
    }
  };

  const onComposerKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const tokenEstimate = Math.ceil(draft.length / 4);

  return (
    <section className="kf-chat" aria-label="Knowledge Forge — Chat">
      <header className="kf-chat__head">
        <h1 className="kf-chat__title">Chat</h1>
        <ModeToggle mode={mode} onChange={setMode} />
      </header>

      <div className="kf-chat__messages" data-testid="kf-chat-messages">
        {turns.length === 0 && !loading && (
          <div className="kf-chat__placeholder">
            Ask a question grounded in the indexed corpus. The assistant will
            return inline citations to the chunks it pulled.
          </div>
        )}
        {turns.map((t) => (
          <Turn
            key={t.id}
            turn={t}
            onCitationClick={(c) => {
              // Deep-link to the cited chunk, not just the document — a
              // chat citation always names a specific chunk, and Review
              // can scroll to it via the existing highlight pipeline.
              const qs = new URLSearchParams({ chunk: c.chunk_id });
              navigate(`/kf/review/${c.document_id}?${qs.toString()}`);
            }}
          />
        ))}
        {loading && <div className="kf-chat__loading orb-mono">…thinking</div>}
        {disabled && (
          <div
            className="kf-chat__banner kf-chat__banner--warn"
            role="status"
            data-testid="kf-chat-disabled"
          >
            <strong>Chat disabled.</strong>
            <p>{disabled.message}</p>
            <p className="kf-chat__banner-rem orb-mono">
              {disabled.remediation}
            </p>
          </div>
        )}
        {error && (
          <div className="kf-chat__banner kf-chat__banner--err" role="alert">
            <strong>Chat failed:</strong> {error.message}
          </div>
        )}
      </div>

      <footer className="kf-chat__composer">
        <textarea
          className="kf-chat__input"
          placeholder="Ask a question…  (Enter to send · Shift+Enter for newline)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onComposerKey}
          aria-label="Chat input"
          rows={3}
        />
        <div className="kf-chat__composer-r">
          <span className="orb-mono kf-chat__tokens">
            ≈ {tokenEstimate} tokens
          </span>
          <Btn
            kind="primary"
            icon={OrbI.spark}
            onClick={send}
            disabled={loading || draft.trim().length === 0}
          >
            {loading ? "Sending…" : "Send"}
          </Btn>
        </div>
      </footer>
    </section>
  );
}

function ModeToggle({
  mode,
  onChange,
}: {
  mode: ApiChatMode;
  onChange: (mode: ApiChatMode) => void;
}): ReactElement {
  const options: Array<{ id: ApiChatMode; label: string }> = [
    { id: "rag", label: "RAG" },
    { id: "graph", label: "GraphRAG" },
    { id: "hybrid", label: "Hybrid" },
  ];
  return (
    <div className="kf-chat__modes" role="tablist" aria-label="Chat mode">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="tab"
          aria-selected={mode === o.id}
          className={`kf-chat__mode ${mode === o.id ? "is-on" : ""}`}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Turn({
  turn,
  onCitationClick,
}: {
  turn: ChatTurn;
  onCitationClick: (c: ApiChatCitation) => void;
}): ReactElement {
  return (
    <article
      className={`kf-chat__turn kf-chat__turn--${turn.role}`}
      data-testid={`kf-chat-turn-${turn.role}`}
    >
      <span className={`kf-chat__avatar kf-chat__avatar--${turn.role}`}>
        {turn.role === "user" ? "U" : "A"}
      </span>
      <div className="kf-chat__bubble">
        <p className="kf-chat__text">{turn.text}</p>
        {turn.citations && turn.citations.length > 0 && (
          <ol className="kf-chat__cites" aria-label="Citations">
            {turn.citations.map((c, i) => (
              <li key={c.chunk_id}>
                <button
                  type="button"
                  className="kf-chat__cite-btn orb-mono"
                  onClick={() => onCitationClick(c)}
                  title={c.snippet ?? c.chunk_id}
                >
                  [{i + 1}] {c.document_id} · {c.score.toFixed(3)}
                </button>
              </li>
            ))}
          </ol>
        )}
        {turn.tokenUsage && (
          <div className="orb-mono kf-chat__usage">
            tokens ·{" "}
            {Object.entries(turn.tokenUsage)
              .map(([k, v]) => `${k}:${v}`)
              .join(" · ")}
          </div>
        )}
      </div>
    </article>
  );
}
