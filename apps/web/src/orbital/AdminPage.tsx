import { useEffect, useState } from "react";

import { ApiError, getAdminHITLState, listArchivedDocuments, listAuditEvents } from "../api/client";
import { getApiBaseUrl } from "../api/client";
import { useAdminConfig } from "../api/useAdminConfig";

import { Btn, Icon } from "./atoms";

export interface AdminPageProps {
  onClose: () => void;
}

type AdminTab = "overview" | "audit" | "hitl" | "archive";

/**
 * Compact admin landing — overview tiles + audit log + hitl snapshot +
 * archive list. Tabs match the four admin surfaces the mockup hints at.
 * Heavy operations (unarchive, purge_artifacts, relink) link out to the
 * legacy /admin pages where the full forms live.
 */
export function AdminPage({ onClose }: AdminPageProps) {
  const [tab, setTab] = useState<AdminTab>("overview");
  const admin = useAdminConfig(getApiBaseUrl());

  return (
    <div className="orb-app" style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--orb-bg)" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0 16px",
          height: 44,
          borderBottom: "1px solid var(--orb-rule)",
          background: "var(--orb-bg-elev)",
        }}
      >
        <Icon name="shield" />
        <span style={{ fontWeight: 600 }}>Admin</span>
        <nav style={{ display: "flex", gap: 2, marginLeft: 16 }}>
          {(["overview", "audit", "hitl", "archive"] as AdminTab[]).map((t) => (
            <button
              key={t}
              className={`rwA-navbtn ${tab === t ? "is-active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        <span style={{ flex: 1 }}></span>
        <a
          href="/admin"
          className="orb-btn orb-btn--ghost orb-btn--xs"
          style={{ textDecoration: "none" }}
        >
          legacy /admin <Icon name="ext" />
        </a>
        <button className="sp-x" onClick={onClose} aria-label="Close admin">
          <Icon name="x" />
        </button>
      </header>

      <div className="orb-scroll" style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
        {tab === "overview" && (
          <div>
            <h2 style={{ margin: "0 0 12px", fontSize: 16, fontWeight: 600 }}>Operational overview</h2>
            <p style={{ margin: "0 0 14px", color: "var(--orb-fg-muted)", fontSize: 13 }}>
              Live snapshot from <code className="orb-mono">/admin/config</code> + a quick path into the heavier admin tools below.
            </p>
            {admin.config ? (
              <div className="set-tiles">
                <Tile label="Knowledge layer" value={admin.config.knowledge_layer.enabled ? "enabled" : "off"} state={admin.config.knowledge_layer.enabled ? "ok" : "off"} sub={admin.config.knowledge_layer.neo4j_database} />
                <Tile label="LLM" value={admin.config.llm.model || "—"} state={admin.config.llm.configured ? "ok" : "off"} sub={admin.config.llm.provider_setting} />
                <Tile label="Embeddings" value={admin.config.embeddings.model || "—"} state={admin.config.embeddings.configured ? "ok" : "off"} sub={admin.config.embeddings.configured ? "active" : "VOYAGE_API_KEY unset"} />
                <Tile label="HITL force-auto" value={admin.config.hitl.force_auto_corpus ? "ON" : "off"} state={admin.config.hitl.force_auto_corpus ? "warn" : "off"} sub="ADR-023 §6" />
                <Tile label="Audit" value={admin.config.audit.enabled ? "on" : "off"} state={admin.config.audit.enabled ? "ok" : "off"} sub={admin.config.audit.enabled ? "events stored" : ""} />
                <Tile label="Persistence" value={admin.config.persistence.persistent ? "sqlite" : "memory"} state="ok" sub={admin.config.persistence.persistent ? admin.config.persistence.data_dir : "in-memory"} />
              </div>
            ) : (
              <div className="set-readonly">Loading /admin/config…</div>
            )}
          </div>
        )}
        {tab === "audit" && <AuditTab />}
        {tab === "hitl" && <HitlTab />}
        {tab === "archive" && <ArchiveTab />}
      </div>
    </div>
  );
}

function Tile({ label, value, state, sub }: { label: string; value: string; state: "ok" | "off" | "warn"; sub?: string }) {
  return (
    <div className={`set-tile set-tile--${state}`}>
      <div className="set-tile-t">{label}</div>
      <div className="set-tile-v">{value}</div>
      {sub && <div className="set-tile-d">{sub}</div>}
    </div>
  );
}

function AuditTab() {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [disabled, setDisabled] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const fetchPage = async (append = false, c?: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await listAuditEvents({ cursor: c, limit: 50 });
      setForbidden(false);
      setDisabled(false);
      setItems((prev) =>
        append ? [...prev, ...(response.items as unknown as Array<Record<string, unknown>>)] : (response.items as unknown as Array<Record<string, unknown>>),
      );
      setCursor(response.next_cursor);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setForbidden(true);
      else if (err instanceof ApiError && err.status === 503) setDisabled(true);
      else
        setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchPage(false);
  }, []);

  if (forbidden) return <div className="set-readonly">Forbidden — sign in as admin to view audit events.</div>;
  if (disabled) return <div className="set-readonly">Audit disabled. Set <code className="orb-mono">KW_AUDIT_ENABLED=true</code>.</div>;
  return (
    <div>
      <h2 style={{ margin: "0 0 12px", fontSize: 16, fontWeight: 600 }}>Audit log</h2>
      <p style={{ color: "var(--orb-fg-muted)", fontSize: 12 }}>
        <code className="orb-mono">GET /admin/audit/events</code> · cursor-paginated · click a row to expand.
      </p>
      {error && <div style={{ color: "var(--orb-err-fg)" }}>{error}</div>}
      <table className="cat-tab" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th>Created</th>
            <th>Event</th>
            <th>Actor</th>
            <th>Preview</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => {
            const id = String(item.id ?? i);
            const open = expanded === id;
            const preview = JSON.stringify(item).slice(0, 100);
            return (
              <Row
                key={id}
                open={open}
                onToggle={() => setExpanded(open ? null : id)}
                item={item}
                preview={preview}
              />
            );
          })}
          {items.length === 0 && !loading && (
            <tr>
              <td colSpan={4} style={{ padding: 16, color: "var(--orb-fg-muted)", textAlign: "center" }}>
                No audit events.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <div style={{ display: "flex", alignItems: "center", padding: 10 }}>
        <span style={{ color: "var(--orb-fg-muted)", fontSize: 12 }}>
          {items.length} event(s){loading ? " · loading…" : ""}
        </span>
        <span style={{ flex: 1 }}></span>
        {cursor && (
          <Btn xs onClick={() => void fetchPage(true, cursor)} disabled={loading}>
            Load more
          </Btn>
        )}
      </div>
    </div>
  );
}

function Row({
  open,
  onToggle,
  item,
  preview,
}: {
  open: boolean;
  onToggle: () => void;
  item: Record<string, unknown>;
  preview: string;
}) {
  return (
    <>
      <tr onClick={onToggle}>
        <td className="orb-mono" style={{ fontSize: 10 }}>{String(item.created_at ?? "—")}</td>
        <td className="orb-mono">{String(item.event_name ?? "—")}</td>
        <td>{String(item.actor ?? "—")}</td>
        <td className="orb-mono" style={{ color: "var(--orb-fg-muted)", fontSize: 10 }}>{preview}…</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4} style={{ background: "var(--orb-bg-sunk)" }}>
            <pre className="orb-mono orb-scroll" style={{ margin: 0, padding: 12, maxHeight: 280, fontSize: 11, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(item, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

function HitlTab() {
  const [state, setState] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [disabled, setDisabled] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const result = await getAdminHITLState();
        setState(result as unknown as Record<string, unknown>);
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else if (err instanceof ApiError && err.status === 503) setDisabled(true);
        else setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (forbidden) return <div className="set-readonly">Forbidden — sign in as admin.</div>;
  if (disabled) return <div className="set-readonly">HITL disabled. Set <code className="orb-mono">KW_HITL_ENABLE_SCORER=true</code>.</div>;
  return (
    <div>
      <h2 style={{ margin: "0 0 12px", fontSize: 16, fontWeight: 600 }}>HITL routing</h2>
      <p style={{ color: "var(--orb-fg-muted)", fontSize: 12 }}>
        <code className="orb-mono">GET /admin/hitl/state</code> — full report in legacy <a href="/admin/hitl">/admin/hitl</a>.
      </p>
      {loading && <div className="set-readonly">Loading…</div>}
      {error && <div style={{ color: "var(--orb-err-fg)" }}>{error}</div>}
      {state && (
        <pre className="orb-mono orb-scroll" style={{ background: "var(--orb-bg-sunk)", padding: 12, borderRadius: 6, maxHeight: 480, fontSize: 11, whiteSpace: "pre-wrap" }}>
          {JSON.stringify(state, null, 2)}
        </pre>
      )}
    </div>
  );
}

function ArchiveTab() {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const response = await listArchivedDocuments();
        setItems(response.items as unknown as Array<Record<string, unknown>>);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <div>
      <h2 style={{ margin: "0 0 12px", fontSize: 16, fontWeight: 600 }}>Archive</h2>
      <p style={{ color: "var(--orb-fg-muted)", fontSize: 12 }}>
        <code className="orb-mono">GET /admin/archive/archived_documents</code> · unarchive/relink/purge in legacy <a href="/admin/archive">/admin/archive</a>.
      </p>
      {loading && <div className="set-readonly">Loading…</div>}
      {error && <div style={{ color: "var(--orb-err-fg)" }}>{error}</div>}
      {!loading && items.length === 0 && <div className="set-readonly">No archived documents.</div>}
      <table className="cat-tab" style={{ marginTop: 8 }}>
        <tbody>
          {items.map((row, i) => (
            <tr key={i}>
              <td>{String((row as Record<string, unknown>).original_filename ?? "—")}</td>
              <td className="orb-mono">{String((row as Record<string, unknown>).document_id ?? "—").slice(0, 12)}</td>
              <td className="orb-mono" style={{ color: "var(--orb-fg-dim)" }}>{String((row as Record<string, unknown>).archived_at ?? "—")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
