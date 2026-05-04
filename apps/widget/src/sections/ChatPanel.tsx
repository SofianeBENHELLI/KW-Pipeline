/**
 * Grounded chat section — Phase 3 follow-up (ADR-016) for the widget.
 *
 * Mirrors the web ``<ChatPanel/>`` (apps/web/src/features/chat/) so
 * 3DEXPERIENCE users get the same chat surface the standalone web app
 * exposes. Self-contained: owns its question, mode toggle, abort
 * controller, and response.
 *
 * Three failure modes the UI surfaces explicitly:
 *
 * - **Phase 3 disabled** — backend returns 503 + ``KW_CHAT_DISABLED``.
 *   The route's ``ApiError`` envelope ships a remediation string;
 *   we render it verbatim so operators see exactly which env vars to
 *   set.
 * - **Network / 5xx** — generic error banner with the message the API
 *   returned.
 * - **Empty answer** — backend returned 200 with an empty answer; we
 *   surface a neutral hint rather than an empty bubble.
 */

import React, { useRef, useState, type FormEvent } from "react";

import { ApiError, askKnowledgeChat } from "../api/client";
import type { ChatCitation, ChatMode, ChatResponse } from "../api/types";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";

const DEFAULT_TOP_K = 5;

const MODES: { id: ChatMode; label: string; hint: string }[] = [
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

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
  /**
   * Optional click hook for navigating to a citation — wired by the
   * parent when integration with the documents view lands.
   */
  onSelectCitation?: (citation: ChatCitation) => void;
}

export const ChatPanel: React.FC<Props> = ({
  apiBaseUrl,
  refreshTick: _refreshTick,
  onSelectCitation,
}) => {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<ChatMode>("rag");
  const [response, setResponse] = useState<ChatResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | string | null>(null);

  // Abort the in-flight request when the user submits a new question
  // before the previous response lands — the older slow response would
  // otherwise race in and overwrite the newer one.
  const abortRef = useRef<AbortController | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (trimmed === "") return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);

    askKnowledgeChat(trimmed, {
      mode,
      top_k: DEFAULT_TOP_K,
      baseUrl: apiBaseUrl,
      signal: controller.signal,
    })
      .then((res) => {
        if (controller.signal.aborted) return;
        setResponse(res);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if ((err as { name?: string })?.name === "AbortError") return;
        if (err instanceof ApiError) {
          setError(err);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Chat request failed.");
        }
        setResponse(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  };

  const isDisabled =
    error instanceof ApiError && error.code === "KW_CHAT_DISABLED";

  const meta =
    response !== null && response.citations.length > 0
      ? `${response.citations.length} citation${response.citations.length === 1 ? "" : "s"} · ${response.llm_model}`
      : undefined;

  return (
    <section
      className="kw-section"
      aria-label="Knowledge chat"
      data-testid="chat-panel"
    >
      <SectionHeader icon="search" title="Chat" meta={meta} />

      <div
        className="kw-seg"
        role="tablist"
        aria-label="Chat retrieval mode"
      >
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="tab"
            aria-selected={mode === m.id}
            aria-label={`${m.label}: ${m.hint}`}
            className={mode === m.id ? "kw-seg__btn kw-seg__btn--active" : "kw-seg__btn"}
            disabled={loading}
            onClick={() => setMode(m.id)}
            data-testid={`chat-mode-${m.id}`}
          >
            {m.label}
          </button>
        ))}
      </div>

      <form className="kw-chat__form" onSubmit={onSubmit}>
        <textarea
          className="kw-chat__input"
          placeholder="Ask a question grounded in the validated documents…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={2}
          aria-label="Question"
          data-testid="chat-panel-input"
        />
        <button
          type="submit"
          className="kw-btn kw-btn--primary"
          disabled={loading || question.trim() === ""}
          data-testid="chat-panel-submit"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      {isDisabled && error instanceof ApiError && (
        <div className="kw-empty" role="status" data-testid="chat-panel-disabled">
          <span className="kw-empty__glyph" aria-hidden="true">
            <Icon name="info" size={18} />
          </span>
          <div className="kw-empty__title">Grounded chat is disabled</div>
          <div className="kw-empty__body">{error.detail}</div>
          {error.remediation !== null && (
            <div className="kw-empty__body kw-search__remediation">
              {error.remediation}
            </div>
          )}
        </div>
      )}

      {error !== null && !isDisabled && (
        <div className="kw-error" role="alert" data-testid="chat-panel-error">
          {error instanceof ApiError
            ? `${error.code}: ${error.detail}`
            : error}
        </div>
      )}

      {loading && <div className="kw-status">Asking…</div>}

      {response !== null && error === null && (
        <article className="kw-chat__answer" data-testid="chat-panel-answer">
          {response.answer.trim() === "" ? (
            <p className="kw-status" data-testid="chat-panel-empty-answer">
              The model did not produce an answer.
            </p>
          ) : (
            <p className="kw-chat__answer-text">{response.answer}</p>
          )}

          {response.warnings.length > 0 && (
            <div
              className="kw-chat__warnings"
              role="status"
              data-testid="chat-panel-warnings"
            >
              <strong>Unresolved citations:</strong>
              <ul>
                {response.warnings.map((marker) => (
                  <li key={marker}>
                    <code>{marker}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {response.citations.length > 0 && (
            <>
              <h3 className="kw-chat__citations-heading">Citations</h3>
              <ol className="kw-search-results" data-testid="chat-panel-citations">
                {response.citations.map((citation) => {
                  const score = (citation.score * 100).toFixed(1);
                  const interactive = onSelectCitation !== undefined;
                  const body = (
                    <>
                      <div className="kw-search-results__meta">
                        <span className="kw-search-results__score">{score}%</span>
                        <code className="kw-search-results__id">
                          {citation.chunk_id}
                        </code>
                      </div>
                      {citation.snippet !== null && citation.snippet !== "" && (
                        <p className="kw-search-results__snippet">
                          {citation.snippet}
                        </p>
                      )}
                      <p className="kw-search-results__loc">
                        document <code>{citation.document_id}</code> · version{" "}
                        <code>{citation.version_id}</code>
                      </p>
                    </>
                  );
                  return (
                    <li
                      key={citation.chunk_id}
                      className="kw-search-results__item"
                      data-testid="chat-panel-citation"
                    >
                      {interactive ? (
                        <button
                          type="button"
                          className="kw-search-results__btn"
                          onClick={() => onSelectCitation(citation)}
                        >
                          {body}
                        </button>
                      ) : (
                        body
                      )}
                    </li>
                  );
                })}
              </ol>
            </>
          )}
        </article>
      )}
    </section>
  );
};
