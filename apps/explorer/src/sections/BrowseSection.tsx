/**
 * Browse — the entry surface of the Knowledge Explorer.
 *
 * Lists every document the catalog knows about, lets the user filter by:
 *
 *   * filename substring (server-side `q=` for typing performance);
 *   * latest-version lifecycle status;
 *   * document kind (PDF / Word / PowerPoint / …) — derived client-side
 *     from `content_type` + filename so the user can scope by input type;
 *   * `document_type` from the semantic profile (the "structure" facet);
 *   * topic labels emitted by the knowledge-graph projection (the
 *     "taxonomy" facet).
 *
 * Picking a row hands the document id back to the App shell which
 * switches to the Document mode.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, getDocumentGraph, getSemantic, listDocuments } from "../api/client";
import type {
  Document,
  DocumentVersion,
  KnowledgeGraphProjection,
  SemanticDocument,
} from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { extOf, FileTypeIcon } from "../components/FileTypeIcon";
import { Icon } from "../components/icons";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import {
  buildHistogram,
  type DocumentFacets,
  deriveFacets,
  EMPTY_FILTER,
  type BrowseFilter,
  matchesFilter,
} from "../state/document-facets";
import {
  type DocumentKind,
  KIND_LABELS,
} from "../viewers/document-kind";

// Cap parallel facet fetches so 100+ documents don't fan out 200 in-flight
// requests on first render. The widget is a tile, not a batch tool — keep
// the network footprint polite.
const FACET_FETCH_CONCURRENCY = 4;

interface Props {
  apiBaseUrl: string;
  refreshTick: number;
  onPickDocument: (documentId: string) => void;
}

export const BrowseSection: React.FC<Props> = ({
  apiBaseUrl,
  refreshTick,
  onPickDocument,
}) => {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<BrowseFilter>(EMPTY_FILTER);
  const [facetsByDocument, setFacetsByDocument] = useState<Map<string, DocumentFacets>>(
    () => new Map(),
  );

  // Pull the catalog. Server-side filter on `q` only — kinds / topics
  // are local because they're derived client-side from the semantic
  // + graph projections.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setLoadError(null);
    listDocuments({ baseUrl: apiBaseUrl, limit: 100, q: filter.q, signal: controller.signal })
      .then((page) => {
        if (cancelled) return;
        setDocuments(page.items);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        const message = err instanceof Error ? err.message : "Failed to load documents.";
        setLoadError(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [apiBaseUrl, refreshTick, filter.q]);

  // Lazily fetch semantic + graph for each document so the facet menus
  // (`document_type`, topics) populate. Throttled to a small concurrency
  // ceiling — rapid catalog growth must not melt the backend.
  const inFlightFacetFetches = useRef<Set<string>>(new Set());
  useEffect(() => {
    let cancelled = false;
    const queue = documents.filter(
      (d) => !facetsByDocument.has(d.id) && !inFlightFacetFetches.current.has(d.id),
    );
    if (queue.length === 0) return;

    const runOne = async (document: Document) => {
      inFlightFacetFetches.current.add(document.id);
      const latest = document.versions.find((v) => v.id === document.latest_version_id);
      let semantic: SemanticDocument | null = null;
      let projection: KnowledgeGraphProjection | null = null;
      try {
        if (latest) {
          semantic = await getSemantic(document.id, latest.id, { baseUrl: apiBaseUrl }).catch(
            (e: unknown) => {
              if (e instanceof ApiError && e.status === 404) return null;
              throw e;
            },
          );
        }
      } catch {
        // Tolerant: missing semantic just means the doc has fewer facets.
      }
      try {
        projection = await getDocumentGraph(document.id, { baseUrl: apiBaseUrl }).catch(
          (e: unknown) => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          },
        );
      } catch {
        // Tolerant: knowledge layer may be disabled.
      }
      if (cancelled) return;
      const facets = deriveFacets(document, semantic, projection);
      setFacetsByDocument((prev) => {
        const next = new Map(prev);
        next.set(document.id, facets);
        return next;
      });
      inFlightFacetFetches.current.delete(document.id);
    };

    // Drain the queue in small parallel batches.
    const drain = async () => {
      for (let i = 0; i < queue.length; i += FACET_FETCH_CONCURRENCY) {
        if (cancelled) return;
        const slice = queue.slice(i, i + FACET_FETCH_CONCURRENCY);
        await Promise.all(slice.map(runOne));
      }
    };
    void drain();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, documents, facetsByDocument]);

  const histogram = useMemo(
    () => buildHistogram(documents, facetsByDocument),
    [documents, facetsByDocument],
  );

  const filteredDocuments = useMemo(
    () =>
      documents.filter((d) => {
        const facets = facetsByDocument.get(d.id);
        // Documents whose facets haven't loaded yet pass the filter when
        // no facet-based filter is selected. Once a facet filter is on,
        // we wait until facets resolve before deciding.
        if (!facets) {
          const usingFacetFilters =
            filter.kinds.length + filter.documentTypes.length + filter.topics.length > 0;
          return !usingFacetFilters;
        }
        return matchesFilter(d, facets, filter);
      }),
    [documents, facetsByDocument, filter],
  );

  const updateFilter = useCallback((patch: Partial<BrowseFilter>) => {
    setFilter((prev) => ({ ...prev, ...patch }));
  }, []);

  return (
    <section className="kw-section" aria-labelledby="browse-section-title">
      <SectionHeader
        icon="files"
        title="Browse documents"
        meta={`${filteredDocuments.length} of ${documents.length}`}
      />
      <div id="browse-section-title" className="visually-hidden">
        Browse documents
      </div>

      <FilterBar
        filter={filter}
        histogram={histogram}
        onChange={updateFilter}
      />

      {loading && documents.length === 0 ? (
        <p className="kw-status">Loading documents…</p>
      ) : loadError !== null ? (
        <p className="kw-error" role="alert">
          {loadError}
        </p>
      ) : filteredDocuments.length === 0 ? (
        <EmptyState
          icon="folder"
          title="No documents match the current filters"
          body="Loosen the filters above, or upload more content from the ingestion widget."
        />
      ) : (
        <ul className="kx-doclist" aria-label="Document catalog">
          {filteredDocuments.map((doc) => (
            <DocumentCard
              key={doc.id}
              document={doc}
              facets={facetsByDocument.get(doc.id) ?? null}
              onPick={() => onPickDocument(doc.id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
};

// ─── Filter bar ──────────────────────────────────────────────────────────────

interface FilterBarProps {
  filter: BrowseFilter;
  histogram: ReturnType<typeof buildHistogram>;
  onChange: (patch: Partial<BrowseFilter>) => void;
}

const KIND_OPTIONS: DocumentKind[] = [
  "pdf",
  "word",
  "powerpoint",
  "excel",
  "image",
  "text",
  "markdown",
  "wiki",
  "html",
  "json",
  "binary",
];

const STATUS_OPTIONS: DocumentVersion["status"][] = [
  "VALIDATED",
  "NEEDS_REVIEW",
  "SEMANTIC_READY",
  "EXTRACTED",
  "STORED",
  "FAILED",
  "REJECTED",
];

const FilterBar: React.FC<FilterBarProps> = ({ filter, histogram, onChange }) => {
  const documentTypeOptions = useMemo(
    () =>
      Array.from(histogram.documentTypes.entries()).sort(
        ([, a], [, b]) => b - a,
      ),
    [histogram],
  );
  const topicOptions = useMemo(
    () =>
      Array.from(histogram.topics.entries()).sort(([, a], [, b]) => b - a).slice(0, 24),
    [histogram],
  );

  return (
    <div className="kx-filterbar" role="group" aria-label="Document filters">
      <div className="kw-search">
        <Icon name="search" />
        <input
          type="search"
          className="kw-input"
          placeholder="Filter by filename…"
          value={filter.q}
          onChange={(e) => onChange({ q: e.target.value })}
          aria-label="Filter by filename"
        />
      </div>

      <FacetGroup
        label="Type"
        options={KIND_OPTIONS.map((id) => ({
          id,
          label: KIND_LABELS[id],
          count: histogram.kinds.get(id) ?? 0,
        }))}
        selected={filter.kinds as string[]}
        onToggle={(id) => onChange({ kinds: toggle(filter.kinds, id as DocumentKind) })}
      />

      {documentTypeOptions.length > 0 && (
        <FacetGroup
          label="Structure"
          options={documentTypeOptions.map(([id, count]) => ({ id, label: id, count }))}
          selected={filter.documentTypes}
          onToggle={(id) => onChange({ documentTypes: toggle(filter.documentTypes, id) })}
        />
      )}

      {topicOptions.length > 0 && (
        <FacetGroup
          label="Taxonomy"
          options={topicOptions.map(([id, count]) => ({ id, label: id, count }))}
          selected={filter.topics}
          onToggle={(id) => onChange({ topics: toggle(filter.topics, id) })}
        />
      )}

      <FacetGroup
        label="Status"
        options={STATUS_OPTIONS.map((id) => ({
          id,
          label: id.replace(/_/g, " ").toLowerCase(),
          count: histogram.statuses.get(id) ?? 0,
        }))}
        selected={filter.statuses as string[]}
        onToggle={(id) =>
          onChange({ statuses: toggle(filter.statuses, id as DocumentVersion["status"]) })
        }
      />

      {(filter.kinds.length +
        filter.documentTypes.length +
        filter.topics.length +
        filter.statuses.length >
        0 ||
        filter.q.length > 0) && (
        <button
          type="button"
          className="kw-btn kw-btn--ghost kw-btn--sm"
          onClick={() => onChange(EMPTY_FILTER)}
        >
          Reset filters
        </button>
      )}
    </div>
  );
};

interface FacetOption {
  id: string;
  label: string;
  count: number;
}

interface FacetGroupProps {
  label: string;
  options: FacetOption[];
  selected: string[];
  onToggle: (id: string) => void;
}

const FacetGroup: React.FC<FacetGroupProps> = ({ label, options, selected, onToggle }) => {
  const visible = options.filter((o) => o.count > 0 || selected.includes(o.id));
  if (visible.length === 0) return null;
  return (
    <div className="kx-facet">
      <span className="kx-facet__label">{label}</span>
      <div className="kx-facet__chips" role="group" aria-label={`${label} filters`}>
        {visible.map((opt) => {
          const active = selected.includes(opt.id);
          return (
            <button
              key={opt.id}
              type="button"
              className={`kx-chip${active ? " kx-chip--active" : ""}`}
              aria-pressed={active}
              onClick={() => onToggle(opt.id)}
            >
              {opt.label}
              <span className="kx-chip__count">{opt.count}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
};

function toggle<T>(values: T[], item: T): T[] {
  const idx = values.indexOf(item);
  if (idx < 0) return [...values, item];
  const next = values.slice();
  next.splice(idx, 1);
  return next;
}

// ─── Document card ──────────────────────────────────────────────────────────

interface DocumentCardProps {
  document: Document;
  facets: DocumentFacets | null;
  onPick: () => void;
}

const DocumentCard: React.FC<DocumentCardProps> = ({ document, facets, onPick }) => {
  const latest = document.versions.find((v) => v.id === document.latest_version_id);
  const ext = extOf(document.original_filename);
  return (
    <li className="kx-doc">
      <button type="button" className="kx-doc__btn" onClick={onPick}>
        <FileTypeIcon ext={ext} />
        <div className="kx-doc__main">
          <div className="kx-doc__title">{document.original_filename}</div>
          <div className="kx-doc__meta">
            {latest && <StatusBadge status={latest.status} />}
            {facets?.documentType && (
              <span className="kx-doc__chip">{facets.documentType}</span>
            )}
            {facets?.topics.slice(0, 3).map((t) => (
              <span key={t} className="kx-doc__chip kx-doc__chip--soft">
                {t}
              </span>
            ))}
            {facets && facets.topics.length > 3 && (
              <span className="kw-mono kw-mono--muted">
                +{facets.topics.length - 3} topics
              </span>
            )}
          </div>
        </div>
        <Icon name="arrow-down" size={14} className="kx-doc__chevron" />
      </button>
    </li>
  );
};
