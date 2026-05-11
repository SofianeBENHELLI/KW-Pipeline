import { useState } from "react";

import { ApiError, askKnowledgeChat } from "../api/client";
import type { ApiChatMode, ApiChatResponse } from "../api/types";

import { Btn, Icon } from "./atoms";

const MODE_LABEL: Record<ApiChatMode, string> = {
  rag: "RAG",
  graph: "GraphRAG",
  hybrid: "Hybrid",
};

interface Turn {
  who: "user" | "bot";
  text: string;
  mode?: ApiChatMode;
  response?: ApiChatResponse;
  ts: number;
}

export interface ChatPageProps {
  onOpenDocument: (id: string) => void;
  onClose: () => void;
}

/**
 * `ChatPanel` from the mockup wired to `POST /knowledge/chat`. Layout
 * matches the mockup verbatim: user bubble + bot card with mode tag +
 * answer + citation list at the bottom. ⌘↵ sends. 503 surfaces the
 * disabled remediation.
 */
export function ChatPage({ onOpenDocument, onClose }: ChatPageProps) {
  const [mode, setMode] = useState<ApiChatMode>("hybrid");
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState(false);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    const question = draft.trim();
    if (!question || pending) return;
    setTurns((t) => [...t, { who: "user", text: question, ts: Date.now() }]);
    setDraft("");
    setPending(true);
    setError(null);
    setDisabled(false);
    try {
      const response = await askKnowledgeChat(question, { mode, top_k: 8 });
      setTurns((t) => [...t, { who: "bot", text: response.answer, mode, response, ts: Date.now() }]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setDisabled(true);
      } else {
        const message =
          err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
        setError(message);
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="orb-app cp">
      <header className="cp-h">
        <span style={{ display: "inline-flex" }}>
          <Icon name="chat" />
        </span>
        <span style={{ fontWeight: 600 }}>Grounded chat</span>
        <span className="cp-mtoggle">
          {(Object.keys(MODE_LABEL) as ApiChatMode[]).map((m) => (
            <button key={m} className={mode === m ? "is-on" : ""} onClick={() => setMode(m)}>
              {MODE_LABEL[m]}
            </button>
          ))}
        </span>
        <span style={{ flex: 1 }}></span>
        <span className="cp-models orb-mono">
          {(() => {
            for (let i = turns.length - 1; i >= 0; i--) {
              if (turns[i].who === "bot" && turns[i].response) return turns[i].response!.llm_model;
            }
            return "claude / voyage";
          })()}
        </span>
        <button className="sp-x" onClick={onClose} aria-label="Close chat panel">
          <Icon name="x" />
        </button>
      </header>

      <div className="cp-body orb-scroll">
        {turns.length === 0 && !disabled && !error && (
          <div style={{ padding: 24, color: "var(--orb-fg-muted)", fontSize: 13, textAlign: "center" }}>
            Ask a question grounded in the corpus. Answers cite specific chunks.
          </div>
        )}
        {disabled && (
          <div style={{ padding: 16, color: "var(--orb-err-fg)", background: "var(--orb-err-bg)", borderRadius: 8, fontSize: 13 }}>
            <b>Chat disabled.</b> Set <code className="orb-mono">VOYAGE_API_KEY</code> and an LLM provider key
            (<code className="orb-mono">ANTHROPIC_API_KEY</code> or <code className="orb-mono">GEMINI_API_KEY</code>) on the backend.
          </div>
        )}
        {error && (
          <div style={{ padding: 12, color: "var(--orb-err-fg)", background: "var(--orb-err-bg)", borderRadius: 6, fontSize: 12 }} role="alert">
            {error}
          </div>
        )}
        {turns.map((t, i) =>
          t.who === "user" ? (
            <div key={i} className="cp-user">
              <div className="cp-bubble">{t.text}</div>
              <div className="cp-userMeta orb-mono">you · {new Date(t.ts).toLocaleTimeString()}</div>
            </div>
          ) : (
            <div key={i} className="cp-bot">
              <div className="cp-botHead">
                <span className="cp-botMark">○</span>
                <span style={{ fontWeight: 600 }}>Orbital</span>
                <span className="cp-tag orb-mono">{MODE_LABEL[t.mode ?? "hybrid"]}</span>
                {t.response && t.response.citations.length > 0 && (
                  <span className="cp-tag orb-mono" style={{ color: "var(--orb-ok)" }}>
                    ● {t.response.citations.length} citations · grounded
                  </span>
                )}
                <span style={{ flex: 1 }}></span>
                <span className="orb-mono cp-time">{t.response?.llm_model ?? ""}</span>
              </div>
              <div className="cp-ans">{t.text || "(no answer)"}</div>
              {t.response && t.response.citations.length > 0 && (
                <div className="cp-cites">
                  <div className="orb-section-h" style={{ marginBottom: 6 }}>
                    Citations
                  </div>
                  {t.response.citations.map((c, ci) => (
                    <div key={`${c.chunk_id}-${ci}`} className="cp-cite-row">
                      <span className="cp-cite-n">{ci + 1}</span>
                      <span className="cp-cite-doc">{c.document_id.slice(0, 8)}</span>
                      <span className="cp-cite-meta">
                        {c.chunk_id.slice(0, 10)} · score {c.score.toFixed(3)}
                      </span>
                      <span className="cp-cite-snip">"{c.snippet ?? "—"}"</span>
                      <button className="sp-jump" onClick={() => onOpenDocument(c.document_id)}>
                        open <Icon name="ext" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ),
        )}
      </div>

      <div className="cp-composer">
        <textarea
          placeholder="Ask a follow-up — answers are grounded in cited chunks…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void ask();
            }
          }}
        />
        <div className="cp-composer-bar">
          <span className="orb-mono" style={{ color: "var(--orb-fg-dim)", fontSize: 10 }}>
            POST /knowledge/chat · mode={mode} · top_k=8
          </span>
          <span style={{ flex: 1 }}></span>
          <span className="cp-hint">⌘↵ to send</span>
          <Btn kind="primary" icon={<Icon name="bolt" />} onClick={() => void ask()} disabled={pending || !draft.trim()}>
            {pending ? "Thinking…" : "Ask"}
          </Btn>
        </div>
      </div>
    </div>
  );
}
