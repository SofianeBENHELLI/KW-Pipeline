import { latestVersion, type PipelineDocument } from "../../domain/document";
import { StatusBadge } from "../../ui/StatusBadge";

interface ReviewWorkspaceProps {
  document: PipelineDocument;
}

export function ReviewWorkspace({ document }: ReviewWorkspaceProps) {
  const version = latestVersion(document);
  const semantic = document.semantic;

  return (
    <section className="workspace" aria-labelledby="workspace-title">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Document detail</p>
          <h2 id="workspace-title">{document.original_filename}</h2>
          <p className="muted">
            Version {version.version_number} - SHA-256 {version.sha256.slice(0, 12)}
          </p>
        </div>
        <StatusBadge status={version.status} />
      </header>

      {version.failure_reason ? (
        <div className="notice danger" role="status">
          <strong>Extraction failed</strong>
          <span>{version.failure_reason}</span>
        </div>
      ) : null}

      <div className="workspace-grid">
        <article className="panel">
          <div className="panel-heading">
            <h3>Raw extraction</h3>
          </div>
          <pre>{document.extraction_text || "No extraction output is available."}</pre>
        </article>

        <article className="panel">
          <div className="panel-heading">
            <h3>Semantic output</h3>
          </div>
          {semantic ? (
            <dl className="semantic-list">
              <div>
                <dt>Validation</dt>
                <dd>{semantic.validation_status}</dd>
              </div>
              <div>
                <dt>Sections</dt>
                <dd>{semantic.sections.length}</dd>
              </div>
              <div>
                <dt>Lineage</dt>
                <dd>
                  {semantic.sections.flatMap((section) => section.source_references).length}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="muted">Semantic output has not been generated.</p>
          )}
        </article>

        <article className="panel markdown-panel">
          <div className="panel-heading">
            <h3>Markdown preview</h3>
          </div>
          <pre>{semantic?.markdown ?? "Markdown preview is not available."}</pre>
        </article>
      </div>

      <footer className="review-actions" aria-label="Review actions">
        <textarea placeholder="Reviewer note" aria-label="Reviewer note" />
        <div className="action-row">
          <button className="secondary-button" type="button">
            Reject
          </button>
          <button className="primary-button" type="button">
            Validate
          </button>
        </div>
      </footer>
    </section>
  );
}
