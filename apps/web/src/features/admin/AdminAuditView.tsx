/**
 * Admin UI — Audit Log Viewer (#206 follow-up).
 *
 * Read-only operator surface over the structured audit event store:
 *
 * 1. **Filter bar** — event-name dropdown (populated from the
 *    response's ``available_event_names``), actor text input, since
 *    + until datetime inputs. "Apply" re-fetches with the filter set;
 *    "Reset" clears every filter and reloads.
 *
 * 2. **Table** — newest-first rows showing relative timestamp (with
 *    absolute ISO on hover), monospace event name, actor (or "—"),
 *    and a truncated payload preview. Clicking a row expands a
 *    panel beneath it with the full structured-logging payload
 *    rendered as pretty JSON.
 *
 * 3. **Pagination** — cursor-based; "Load more" appends the next
 *    page to the existing rows so an operator can scroll back
 *    through history without losing place.
 *
 * Same auth posture as :file:`AdminArchiveView` / :file:`AdminHITLView`:
 * we never derive role client-side. A 403 envelope from the API
 * collapses the page to a "Forbidden" state; a 503
 * ``KW_AUDIT_DISABLED`` envelope renders a dedicated "Audit log
 * disabled" card with the envelope's remediation hint.
 *
 * UX decisions worth flagging:
 *
 * - Payload preview truncates at ~80 characters of the JSON
 *   serialization. The full payload is always one click away in the
 *   expanded panel; we truncate aggressively so the row stays scannable.
 * - Filter combinator is AND across every filled field (matches the
 *   route's behaviour). Empty fields are treated as "no constraint",
 *   not "filter to null".
 * - The dropdown's "All events" option is the empty-string sentinel;
 *   the `event_name` query param is dropped server-side when the
 *   value is empty so the route never sees a literal "" filter.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, listAuditEvents } from "../../api/client";
import type {
  ApiAdminAuditEventsResponse,
  ApiAuditEventItem,
} from "../../api/types";

// ─── Constants ──────────────────────────────────────────────────────────────

/** Per-page limit. Mirrors the route's default; the upper bound is 200
 *  server-side so a tweak here doesn't accidentally exceed the API
 *  contract. */
const PAGE_LIMIT = 50;

/** Payload preview cap (chars). Aggressive — the full payload is one
 *  click away in the expanded row panel, so the cell only needs
 *  enough text to disambiguate similar-looking events. */
const PAYLOAD_PREVIEW_LIMIT = 80;

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Format an ISO timestamp as a relative phrase. The cell's title
 *  attribute carries the absolute ISO so a hover surfaces the exact
 *  moment without expanding the row. */
export function formatRelativeTimestamp(
  isoString: string,
  now: Date = new Date(),
): string {
  const then = new Date(isoString);
  const ms = now.getTime() - then.getTime();
  if (Number.isNaN(ms) || ms < 0) return isoString;
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
  return then.toISOString().slice(0, 10);
}

/** Truncated payload preview — the JSON serialisation of the row's
 *  payload, capped at ``PAYLOAD_PREVIEW_LIMIT`` chars with an ellipsis
 *  marker so an operator can tell a long payload was clipped. */
export function payloadPreview(payload: Record<string, unknown>): string {
  const json = JSON.stringify(payload);
  if (json.length <= PAYLOAD_PREVIEW_LIMIT) return json;
  return `${json.slice(0, PAYLOAD_PREVIEW_LIMIT)}…`;
}

// ─── Filter state ───────────────────────────────────────────────────────────

interface FilterState {
  eventName: string;
  actor: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: FilterState = {
  eventName: "",
  actor: "",
  since: "",
  until: "",
};

/** Drop empty fields so the API client doesn't send literal empty
 *  strings — the route would treat them as the "no filter" sentinel
 *  but we'd rather keep the URL clean. */
function filtersToOptions(filters: FilterState): {
  eventName?: string;
  actor?: string;
  since?: string;
  until?: string;
} {
  const opts: {
    eventName?: string;
    actor?: string;
    since?: string;
    until?: string;
  } = {};
  if (filters.eventName) opts.eventName = filters.eventName;
  if (filters.actor) opts.actor = filters.actor;
  if (filters.since) opts.since = filters.since;
  if (filters.until) opts.until = filters.until;
  return opts;
}

function filtersAreEmpty(filters: FilterState): boolean {
  return (
    !filters.eventName && !filters.actor && !filters.since && !filters.until
  );
}

// ─── Main view ──────────────────────────────────────────────────────────────

export function AdminAuditView() {
  // Applied filters — the in-flight set the table reflects. Pending
  // filter edits live in their own local state on the inputs so a
  // user typing in "actor" doesn't hit the network on every keystroke.
  const [appliedFilters, setAppliedFilters] =
    useState<FilterState>(EMPTY_FILTERS);
  const [pendingFilters, setPendingFilters] =
    useState<FilterState>(EMPTY_FILTERS);

  const [items, setItems] = useState<ApiAuditEventItem[]>([]);
  const [availableEventNames, setAvailableEventNames] = useState<string[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadMoreBusy, setLoadMoreBusy] = useState(false);
  const [loadError, setLoadError] = useState<ApiError | string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const applyResponse = useCallback(
    (response: ApiAdminAuditEventsResponse, append: boolean) => {
      setItems((prev) => (append ? [...prev, ...response.items] : response.items));
      setAvailableEventNames(response.available_event_names);
      setNextCursor(response.next_cursor);
    },
    [],
  );

  const loadFirstPage = useCallback(
    async (filters: FilterState) => {
      setLoading(true);
      setLoadError(null);
      // Collapse any expanded row — the row id may not survive the refetch.
      setExpandedId(null);
      try {
        const response = await listAuditEvents({
          ...filtersToOptions(filters),
          limit: PAGE_LIMIT,
        });
        applyResponse(response, false);
      } catch (err: unknown) {
        if (err instanceof ApiError) setLoadError(err);
        else if (err instanceof Error) setLoadError(err.message);
        else setLoadError("Failed to load audit events.");
      } finally {
        setLoading(false);
      }
    },
    [applyResponse],
  );

  const loadMore = useCallback(async () => {
    if (nextCursor === null) return;
    setLoadMoreBusy(true);
    try {
      const response = await listAuditEvents({
        ...filtersToOptions(appliedFilters),
        cursor: nextCursor,
        limit: PAGE_LIMIT,
      });
      applyResponse(response, true);
    } catch (err: unknown) {
      if (err instanceof ApiError) setLoadError(err);
      else if (err instanceof Error) setLoadError(err.message);
      else setLoadError("Failed to load more events.");
    } finally {
      setLoadMoreBusy(false);
    }
  }, [appliedFilters, applyResponse, nextCursor]);

  // Initial load.
  useEffect(() => {
    void loadFirstPage(EMPTY_FILTERS);
  }, [loadFirstPage]);

  const handleApply = useCallback(() => {
    setAppliedFilters(pendingFilters);
    void loadFirstPage(pendingFilters);
  }, [loadFirstPage, pendingFilters]);

  const handleReset = useCallback(() => {
    setPendingFilters(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
    void loadFirstPage(EMPTY_FILTERS);
  }, [loadFirstPage]);

  // Forbidden state — same pattern as AdminArchiveView / AdminHITLView.
  if (loadError instanceof ApiError && loadError.status === 403) {
    return (
      <main className="app-shell admin-shell" aria-label="Admin audit log">
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

  // 503 KW_AUDIT_DISABLED — dedicated state card with remediation hint.
  if (loadError instanceof ApiError && loadError.status === 503) {
    return (
      <main className="app-shell admin-shell" aria-label="Admin audit log">
        <section className="workspace">
          <header className="workspace-header">
            <div>
              <p className="eyebrow">Admin</p>
              <h2>Audit Log</h2>
            </div>
          </header>
          <div
            className="notice danger"
            role="alert"
            data-testid="audit-disabled-state"
          >
            <strong>Audit log disabled.</strong>
            <span>{loadError.detail}</span>
            {loadError.remediation !== null ? (
              <span className="muted">{loadError.remediation}</span>
            ) : null}
          </div>
        </section>
      </main>
    );
  }

  const emptyMessage = filtersAreEmpty(appliedFilters)
    ? "No audit events yet."
    : "No audit events match your filters.";

  return (
    <main className="app-shell admin-shell" aria-label="Admin audit log">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Audit Log</h2>
          </div>
        </header>

        {/* Filter bar. */}
        <div className="audit-filter-bar" data-testid="audit-filter-bar">
          <label>
            <span>Event name</span>
            <select
              value={pendingFilters.eventName}
              onChange={(e) =>
                setPendingFilters((prev) => ({
                  ...prev,
                  eventName: e.target.value,
                }))
              }
              data-testid="filter-event-name"
            >
              <option value="">All events</option>
              {availableEventNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Actor</span>
            <input
              type="text"
              value={pendingFilters.actor}
              onChange={(e) =>
                setPendingFilters((prev) => ({
                  ...prev,
                  actor: e.target.value,
                }))
              }
              placeholder="any"
              data-testid="filter-actor"
            />
          </label>
          <label>
            <span>Since</span>
            <input
              type="datetime-local"
              value={pendingFilters.since}
              onChange={(e) =>
                setPendingFilters((prev) => ({
                  ...prev,
                  since: e.target.value,
                }))
              }
              data-testid="filter-since"
            />
          </label>
          <label>
            <span>Until</span>
            <input
              type="datetime-local"
              value={pendingFilters.until}
              onChange={(e) =>
                setPendingFilters((prev) => ({
                  ...prev,
                  until: e.target.value,
                }))
              }
              data-testid="filter-until"
            />
          </label>
          <div className="filter-buttons">
            <button
              type="button"
              className="primary-button"
              onClick={handleApply}
              disabled={loading}
              data-testid="filter-apply"
            >
              Apply
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={handleReset}
              disabled={loading}
              data-testid="filter-reset"
            >
              Reset
            </button>
          </div>
        </div>

        {loadError !== null && !(loadError instanceof ApiError) ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>{loadError}</span>
          </div>
        ) : loadError instanceof ApiError ? (
          <div className="notice danger" role="alert">
            <strong>Failed to load</strong>
            <span>{loadError.detail}</span>
          </div>
        ) : null}

        {/* Table. */}
        {items.length === 0 ? (
          loading ? (
            <p className="muted" role="status" aria-live="polite">
              Loading…
            </p>
          ) : (
            <p className="muted" data-testid="empty-events">
              {emptyMessage}
            </p>
          )
        ) : (
          <table
            className="admin-audit-table"
            aria-label="Audit events"
          >
            <thead>
              <tr>
                <th scope="col">Timestamp</th>
                <th scope="col">Event name</th>
                <th scope="col">Actor</th>
                <th scope="col">Payload preview</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <AuditEventRow
                  key={item.id}
                  item={item}
                  expanded={expandedId === item.id}
                  onToggle={() =>
                    setExpandedId((cur) => (cur === item.id ? null : item.id))
                  }
                />
              ))}
            </tbody>
          </table>
        )}

        {nextCursor !== null ? (
          <div className="audit-load-more">
            <button
              type="button"
              className="secondary-button"
              onClick={() => void loadMore()}
              disabled={loadMoreBusy}
              data-testid="load-more"
            >
              {loadMoreBusy ? "Loading…" : "Load more"}
            </button>
          </div>
        ) : null}
      </section>
    </main>
  );
}

// ─── Row + expanded payload ─────────────────────────────────────────────────

interface AuditEventRowProps {
  item: ApiAuditEventItem;
  expanded: boolean;
  onToggle: () => void;
}

function AuditEventRow({ item, expanded, onToggle }: AuditEventRowProps) {
  // ``payload`` is typed as ``{ [key: string]: unknown }`` by the
  // OpenAPI codegen — same Record-of-unknowns shape the route surfaces.
  const payload = item.payload as Record<string, unknown>;
  return (
    <>
      <tr
        data-testid="audit-event-row"
        onClick={onToggle}
        // Keyboard a11y — Enter/Space toggles the row the same way a
        // click does so a screen-reader user can expand without a
        // pointer.
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        tabIndex={0}
        role="button"
        aria-expanded={expanded}
        className={expanded ? "expanded" : undefined}
      >
        <td title={item.created_at} data-testid="row-timestamp">
          {formatRelativeTimestamp(item.created_at)}
        </td>
        <td>
          <code data-testid="row-event-name">{item.event_name}</code>
        </td>
        <td data-testid="row-actor">{item.actor ?? "—"}</td>
        <td data-testid="row-payload-preview" className="payload-preview">
          {payloadPreview(payload)}
        </td>
      </tr>
      {expanded ? (
        <tr data-testid="audit-event-row-expanded">
          <td colSpan={4}>
            <pre className="audit-payload-json">
              {JSON.stringify(payload, null, 2)}
            </pre>
          </td>
        </tr>
      ) : null}
    </>
  );
}
