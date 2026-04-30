import "./styles.css";
import { sampleDocuments } from "./fixtures/sampleDocuments";
import { PipelineWidget } from "./features/pipeline/PipelineWidget";
import { ReviewWorkspace } from "./features/review/ReviewWorkspace";

export default function App() {
  const selectedDocument = sampleDocuments[0];

  return (
    <main className="app-shell" aria-label="Orbital document review workbench">
      <PipelineWidget documents={sampleDocuments} selectedDocumentId={selectedDocument.id} />
      <ReviewWorkspace document={selectedDocument} />
    </main>
  );
}
