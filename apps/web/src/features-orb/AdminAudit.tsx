import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, listAuditEvents } from "../api/client";
import type { components } from "../api/generated/schema";
import { Btn, Card, Mono, SectionHeading } from "../ui/orb";
import { Input } from "../ui/orb/atoms";

import { OrbShell } from "./Shell";

type AuditEvent = components["schemas"]["AuditEventItem"];

/**
 * Phase-6 audit viewer. Replicates the existing AdminAuditView's
 * mechanics — event-name dropdown driven by `available_event_names`,
 * actor/since/until filters, cursor pagination via "Load more" — on
 * the new design tokens. Click a row to expand its full payload as
 * pretty JSON.
 */
export function OrbAdminAudit() {
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [available, setAvailable] = useState<string[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [disabled, setDisabled] = useState(false);

  const [eventName, setEventName] = useState("");
  const [actor, setActor] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  const [expanded, setExpanded] = useState<string | null>(null);

  const fetchPage = useCallback(
    async ({ append = false, cursor }: { append?: boolean; cursor?: string } = {}) => {
      setLoading(true);
      setError(null);
      try {
        const response = await listAuditEvents({
          eventName: eventName || undefined,
          actor: actor || undefined,
          since: since || undefined,
          until: until || undefined,
          cursor,
          limit: 50,
        });
        setForbidden(false);
        setDisabled(false);
        setItems((prev) => (append ? [...prev, ...response.items] : response.items));
        setNextCursor(response.next_cursor);
        if (response.available_event_names) setAvailable(response.available_event_names);
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
          setItems([]);
        } else if (err instanceof ApiError && err.status === 503) {
          setDisabled(true);
          setItems([]);
        } else {
          const message =
            err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
          setError(message);
        }
      } finally {
        setLoading(false);
      }
    },
    [eventName, actor, since, until],
  );

  useEffect(() => {
    void fetchPage();
  }, [fetchPage]);

  const eventOptions = useMemo(() => available, [available]);

  return (
    <OrbShell rail={<AuditRail />}>
      <div className="orb-admin">
        <h1 className="orb-admin__title">Audit log</h1>
        <p className="orb-admin__subtitle">
          Every system event, newest first. Click a row to expand its full payload.
        </p>

        <Card className="orb-admin__filters">
          <div className="orb-admin__filter">
            <label htmlFor="orb-audit-event">Event</label>
            <select
              id="orb-audit-event"
              className="orb-input"
              value={eventName}
              onChange={(event) => setEventName(event.target.value)}
            >
              <option value="">All events</option>
              {eventOptions.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </div>
          <div className="orb-admin__filter">
            <label htmlFor="orb-audit-actor">Actor</label>
            <Input
              id="orb-audit-actor"
              type="search"
              placeholder="user id…"
              value={actor}
              onChange={(event) => setActor(event.target.value)}
            />
          </div>
          <div className="orb-admin__filter">
            <label htmlFor="orb-audit-since">Since</label>
            <Input
              id="orb-audit-since"
              type="datetime-local"
              value={since}
              onChange={(event) => setSince(event.target.value)}
            />
          </div>
          <div className="orb-admin__filter">
            <label htmlFor="orb-audit-until">Until</label>
            <Input
              id="orb-audit-until"
              type="datetime-local"
              value={until}
              onChange={(event) => setUntil(event.target.value)}
            />
          </div>
        </Card>

        {forbidden && (
          <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">
            Forbidden — sign in as an admin to view the audit log.
          </div>
        )}
        {disabled && (
          <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">
            Audit store disabled. Set <Mono>KW_AUDIT_ENABLED=true</Mono> on the backend.
          </div>
        )}
        {error && (
          <div className="orb-review__placeholder orb-review__placeholder--error" role="alert">{error}</div>
        )}

        {!forbidden && !disabled && (
          <table className="orb-catalog__table">
            <thead>
              <tr>
                <th style={{ width: 180 }}>Created</th>
                <th style={{ width: 200 }}>Event</th>
                <th style={{ width: 160 }}>Actor</th>
                <th>Payload preview</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const open = expanded === item.id;
                const preview = previewPayload(item);
                return (
                  <RowFragment
                    key={item.id}
                    open={open}
                    onToggle={() => setExpanded(open ? null : item.id)}
                    item={item}
                    preview={preview}
                  />
                );
              })}
              {items.length === 0 && !loading && (
                <tr>
                  <td colSpan={4} className="orb-catalog__empty">
                    No audit events match the current filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}

        <div className="orb-catalog__footer">
          <span>{items.length} event(s){loading ? " · loading…" : ""}</span>
          <span className="orb-catalog__footer-spacer" />
          {nextCursor && (
            <Btn size="xs" onClick={() => void fetchPage({ append: true, cursor: nextCursor })} disabled={loading}>
              Load more
            </Btn>
          )}
        </div>
      </div>
    </OrbShell>
  );
}

function previewPayload(item: AuditEvent): string {
  const payload = (item as unknown as { payload?: unknown }).payload;
  if (payload == null) return "—";
  try {
    const str = typeof payload === "string" ? payload : JSON.stringify(payload);
    return str.length > 80 ? `${str.slice(0, 80)}…` : str;
  } catch {
    return String(payload);
  }
}

function AuditRail() {
  return (
    <div className="orb-rail">
      <div className="orb-rail__head">
        <SectionHeading>Admin</SectionHeading>
      </div>
      <nav className="orb-rail__views" aria-label="Admin navigation">
        <Link to="/orb/admin" className="orb-rail__view">
          ← Back to admin hub
        </Link>
        <Link to="/orb" className="orb-rail__view">
          Catalog
        </Link>
      </nav>
    </div>
  );
}

function RowFragment({
  open,
  onToggle,
  item,
  preview,
}: {
  open: boolean;
  onToggle: () => void;
  item: AuditEvent;
  preview: string;
}) {
  return (
    <>
      <tr className="orb-catalog__row" onClick={onToggle}>
        <td>
          <Mono>{item.created_at}</Mono>
        </td>
        <td>
          <Mono>{item.event_name}</Mono>
        </td>
        <td>{item.actor ?? <span style={{ color: "var(--orb-fg-faint)" }}>—</span>}</td>
        <td className="orb-mono" style={{ color: "var(--orb-fg-muted)" }}>{preview}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4} style={{ background: "var(--orb-bg-sunk)" }}>
            <pre className="orb-review__pre orb-mono orb-scroll" style={{ maxHeight: 360, margin: 0 }}>
              {JSON.stringify(item, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}
