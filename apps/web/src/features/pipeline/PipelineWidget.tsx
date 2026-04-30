import { latestVersion, type PipelineDocument } from "../../domain/document";
import { StatusBadge } from "../../ui/StatusBadge";

interface PipelineWidgetProps {
  documents: PipelineDocument[];
  selectedDocumentId: string;
}

export function PipelineWidget({
  documents,
  selectedDocumentId,
}: PipelineWidgetProps) {
  const pendingReview = documents.filter(
    (document) => latestVersion(document).status === "NEEDS_REVIEW",
  ).length;
  const failed = documents.filter(
    (document) => latestVersion(document).status === "FAILED",
  ).length;
  const duplicates = documents.filter(
    (document) => latestVersion(document).status === "DUPLICATE_DETECTED",
  ).length;

  return (
    <section className="widget-panel" aria-labelledby="pipeline-widget-title">
      <div className="widget-header">
        <div>
          <p className="eyebrow">Orbital</p>
          <h1 id="pipeline-widget-title">KW Pipeline</h1>
        </div>
        <button className="icon-button" type="button" aria-label="Upload document">
          +
        </button>
      </div>

      <div className="metric-grid" aria-label="Pipeline status summary">
        <Metric label="Review" value={pendingReview} tone="warning" />
        <Metric label="Failed" value={failed} tone="danger" />
        <Metric label="Duplicate" value={duplicates} tone="neutral" />
      </div>

      <div className="section-heading">
        <h2>Recent documents</h2>
      </div>

      <div className="document-list">
        {documents.map((document) => {
          const version = latestVersion(document);
          const selected = document.id === selectedDocumentId;

          return (
            <button
              className={selected ? "document-row selected" : "document-row"}
              type="button"
              key={document.id}
              aria-pressed={selected}
            >
              <span>
                <strong>{document.original_filename}</strong>
                <small>v{version.version_number}</small>
              </span>
              <StatusBadge status={version.status} />
            </button>
          );
        })}
      </div>
    </section>
  );
}

interface MetricProps {
  label: string;
  value: number;
  tone: "neutral" | "warning" | "danger";
}

function Metric({ label, value, tone }: MetricProps) {
  return (
    <div className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
