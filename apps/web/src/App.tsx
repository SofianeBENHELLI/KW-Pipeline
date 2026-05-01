import { useEffect, useState } from "react";
import "./styles.css";
import { listDocuments } from "./api/client";
import type { ApiDocument } from "./api/types";
import { PipelineWidget } from "./features/pipeline/PipelineWidget";
import { ReviewWorkspace } from "./features/review/ReviewWorkspace";

export default function App() {
  const [documents, setDocuments] = useState<ApiDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    setLoading(true);
    setError(null);

    listDocuments()
      .then((page) => {
        if (!cancelled) {
          setDocuments(page.items);
          if (page.items.length > 0) {
            setSelectedId(page.items[0].id);
          }
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load documents.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const selectedDocument = documents.find((d) => d.id === selectedId) ?? null;

  if (loading) {
    return (
      <main className="app-shell" aria-label="Orbital document review workbench">
        <p className="muted" role="status" aria-live="polite">
          Loading documents…
        </p>
      </main>
    );
  }

  if (error !== null) {
    return (
      <main className="app-shell" aria-label="Orbital document review workbench">
        <div className="notice danger" role="alert">
          <strong>Failed to load documents</strong>
          <span>{error}</span>
        </div>
      </main>
    );
  }

  return (
    <main className="app-shell" aria-label="Orbital document review workbench">
      <PipelineWidget
        documents={documents}
        selectedDocumentId={selectedId ?? ""}
        onSelectDocument={setSelectedId}
      />
      {selectedDocument !== null ? (
        <ReviewWorkspace document={selectedDocument} />
      ) : (
        <section className="workspace">
          <p className="muted">No documents found. Upload a document to get started.</p>
        </section>
      )}
    </main>
  );
}
