import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PipelineWidget } from "./PipelineWidget";
import type { ApiDocument, ApiUploadResponse } from "../../api/types";

// `openapi-fetch` invokes `fetch` with a Request object — the same helper
// shape as in App.test.tsx / client.test.ts.
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const FIXTURE_DOCUMENT: ApiDocument = {
  id: "doc-001",
  original_filename: "test.txt",
  latest_version_id: "ver-001",
  created_at: "2026-05-01T00:00:00Z",
  versions: [
    {
      id: "ver-001",
      document_id: "doc-001",
      version_number: 1,
      filename: "test.txt",
      content_type: "text/plain",
      file_size: 1000,
      sha256: "abc123def456789012345abcdef1234567890abcdef1234567890abcdef12345",
      storage_uri: "file://test",
      status: "STORED",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-01T00:00:00Z",
    },
  ],
};

const UPLOAD_RESPONSE: ApiUploadResponse = {
  id: "ver-002",
  document_id: "doc-002",
  version_number: 1,
  filename: "newfile.txt",
  content_type: "text/plain",
  file_size: 100,
  sha256: "fedcba0987654321abcdef0123456789abcdef0123456789abcdef0123456789",
  storage_uri: "file://newfile",
  status: "STORED",
  duplicate_of_version_id: null,
  failure_reason: null,
  reviewer_note: null,
  reviewed_at: null,
  created_at: "2026-05-01T00:00:00Z",
};

const DUPLICATE_RESPONSE: ApiUploadResponse = {
  ...UPLOAD_RESPONSE,
  id: "ver-003",
  document_id: "doc-003",
  filename: "dupe.txt",
  status: "DUPLICATE_DETECTED",
  duplicate_of_version_id: "ver-001",
};

describe("PipelineWidget", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders an upload button with a hidden file input that accepts text/pdf/docx", () => {
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId="doc-001"
        onSelectDocument={() => {}}
      />,
    );

    const uploadButton = screen.getByRole("button", { name: /Upload document/i });
    expect(uploadButton).toBeInTheDocument();

    // The hidden input should accept the trio of document mime types.
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    expect(input.accept).toContain("text/plain");
    expect(input.accept).toContain("application/pdf");
    expect(input.accept).toContain(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );
  });

  it("uploads a file on selection and bubbles the new document id", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents/upload")) {
          return Promise.resolve(makeJsonResponse(UPLOAD_RESPONSE));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    const onUploaded = vi.fn();
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId="doc-001"
        onSelectDocument={() => {}}
        onUploaded={onUploaded}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello world"], "newfile.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    // Truncated sha256 (first 12 chars) should appear after success.
    await waitFor(() => {
      expect(screen.getByText(/fedcba098765/)).toBeInTheDocument();
    });
    expect(onUploaded).toHaveBeenCalledWith("doc-002");

    // The full sha256 must NOT be displayed verbatim.
    expect(screen.queryByText(UPLOAD_RESPONSE.sha256)).toBeNull();
  });

  it("renders an inline alert with the API detail when upload fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Unsupported file type." }, 415),
    );

    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["data"], "notes.bin", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/Unsupported file type\./)).toBeInTheDocument();
  });

  it("rejects empty files locally without making a network call", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(UPLOAD_RESPONSE),
    );

    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const empty = new File([], "empty.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [empty] } });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/empty/i)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("highlights duplicate uploads with a prominent marker", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(DUPLICATE_RESPONSE),
    );

    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["dupe"], "dupe.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText(/Duplicate detected/i)).toBeInTheDocument();
    });
  });

  it("disables the upload button while uploading and sets aria-busy", async () => {
    const pending: { resolve: (response: Response) => void } = {
      resolve: () => {},
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          pending.resolve = resolve;
        }),
    );

    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["data"], "test.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    const button = screen.getByRole("button", { name: /Upload document/i });
    await waitFor(() => {
      expect(button).toHaveAttribute("aria-busy", "true");
      expect(button).toBeDisabled();
    });

    // Cleanup: resolve the pending upload.
    pending.resolve(makeJsonResponse(UPLOAD_RESPONSE));
  });
});

describe("PipelineWidget — duplicate marker in row", () => {
  it("shows a 'Duplicate' marker on document rows whose latest version is a duplicate", () => {
    const dupeDoc: ApiDocument = {
      ...FIXTURE_DOCUMENT,
      id: "doc-dupe",
      original_filename: "dupe.txt",
      versions: [
        {
          ...FIXTURE_DOCUMENT.versions[0],
          id: "ver-dupe",
          document_id: "doc-dupe",
          status: "DUPLICATE_DETECTED",
          duplicate_of_version_id: "ver-001",
        },
      ],
      latest_version_id: "ver-dupe",
    };

    render(
      <PipelineWidget
        documents={[dupeDoc]}
        selectedDocumentId="doc-dupe"
        onSelectDocument={() => {}}
      />,
    );

    expect(
      screen.getByLabelText(/Duplicate of an earlier version/i),
    ).toBeInTheDocument();
  });
});
