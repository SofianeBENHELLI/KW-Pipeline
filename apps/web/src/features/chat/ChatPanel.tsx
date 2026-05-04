/**
 * Grounded chat panel — Phase 3 follow-up.
 *
 * Calls ``POST /knowledge/chat`` with the user's question, the
 * selected retrieval mode, and a fixed ``top_k``. Renders the
 * model's free-text answer alongside the chunk citations the prompt
 * was grounded in.
 *
 * Three failure modes the UI surfaces explicitly, mirroring
 * ``<SearchPanel/>``:
 *
 * - **Phase 3 disabled** (503 + ``KW_CHAT_DISABLED``). Render the
 *   route's remediation copy verbatim so operators see exactly which
 *   env vars to set.
 * - **Network / 5xx**. Generic error banner with the message the API
 *   returned.
 * - **Empty answer**. The backend returned 200 with an empty answer —
 *   surface a neutral "no answer" hint rather than an empty bubble.
 */

import { useRef, useState, type FormEvent } from "react";

import { ApiError, askKnowledgeChat } from "../../api/client";
import type {
  ApiChatCitation,
  ApiChatMode,
  ApiChatResponse,
} from "../../api/types";

import { ChatModeToggle } from "./ChatModeToggle";

const DEFAULT_TOP_K = 5;

export interface ChatPanelProps {
  /**
   * Click handler invoked when a citation is activated. Lets the
   * parent navigate to the cited chunk's document/version. Optional —
   * when absent, the citation rows render as informational only.
   */
  onSelectCitation?: (citation: ApiChatCitation) => void;
}

export function ChatPanel({ onSelectCitation }: ChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<ApiChatMode>("rag");
  const [response, setResponse] = useState<ApiChatResponse | null>(null);
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
      signal: controller.signal,
    })
      .then((res) => {
        if (controller.signal.aborted) return;
        setResponse(res);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
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

  return (
    <section
      className="workspace chat-panel"
      aria-label="Knowledge chat"
      data-testid="chat-panel"
    >
      <header className="chat-panel__header">
        <h2>Chat</h2>
        {response !== null && response.citations.length > 0 && (
          <p className="muted small">
            Grounded in {response.citations.length} chunk
            {response.citations.length === 1 ? "" : "s"} · model{" "}
            <code>{response.llm_model}</code>
          </p>
        )}
      </header>

      <ChatModeToggle value={mode} onChange={setMode} disabled={loading} />

      <form className="chat-panel__form" onSubmit={onSubmit}>
        <label className="chat-panel__input">
          <span className="visually-hidden">Question</span>
          <textarea
            placeholder="Ask a question grounded in the validated documents…"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={2}
            aria-label="Question"
            data-testid="chat-panel-input"
          />
        </label>
        <button
          type="submit"
          className="chat-panel__submit"
          disabled={loading || question.trim() === ""}
          data-testid="chat-panel-submit"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      {isDisabled && error instanceof ApiError && (
        <div
          className="chat-panel__notice chat-panel__notice--disabled"
          role="status"
          data-testid="chat-panel-disabled"
        >
          <strong>Grounded chat is disabled.</strong>
          <p>{error.message}</p>
          {error.remediation !== null && <p className="muted">{error.remediation}</p>}
        </div>
      )}

      {error !== null && !isDisabled && (
        <div
          className="chat-panel__notice chat-panel__notice--error"
          role="alert"
          data-testid="chat-panel-error"
        >
          {error instanceof Error ? error.message : error}
        </div>
      )}

      {response !== null && error === null && (
        <article className="chat-panel__answer" data-testid="chat-panel-answer">
          {response.answer.trim() === "" ? (
            <p className="muted" data-testid="chat-panel-empty-answer">
              The model did not produce an answer.
            </p>
          ) : (
            <p className="chat-panel__answer-text">{response.answer}</p>
          )}

          {response.warnings.length > 0 && (
            <div
              className="chat-panel__warnings"
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
              <h3 className="chat-panel__citations-heading">Citations</h3>
              <ol className="chat-panel__citations" data-testid="chat-panel-citations">
                {response.citations.map((citation) => {
                  const score = (citation.score * 100).toFixed(1);
                  const interactive = onSelectCitation !== undefined;
                  return (
                    <li
                      key={citation.chunk_id}
                      className="chat-panel__citation"
                      data-testid="chat-panel-citation"
                    >
                      {interactive ? (
                        <button
                          type="button"
                          className="chat-panel__citation-button"
                          onClick={() => onSelectCitation(citation)}
                        >
                          <CitationBody citation={citation} score={score} />
                        </button>
                      ) : (
                        <CitationBody citation={citation} score={score} />
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
}

function CitationBody({
  citation,
  score,
}: {
  citation: ApiChatCitation;
  score: string;
}) {
  return (
    <>
      <div className="chat-panel__citation-meta">
        <span className="chat-panel__citation-score">{score}%</span>
        <code className="chat-panel__citation-id">{citation.chunk_id}</code>
      </div>
      {citation.snippet !== null && citation.snippet !== "" && (
        <p className="chat-panel__citation-snippet">{citation.snippet}</p>
      )}
      <p className="muted small">
        document <code>{citation.document_id}</code> · version{" "}
        <code>{citation.version_id}</code>
      </p>
    </>
  );
}
