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

describe("PipelineWidget — metric counts", () => {
  function makeDoc(id: string, status: ApiDocument["versions"][number]["status"]): ApiDocument {
    return {
      ...FIXTURE_DOCUMENT,
      id,
      original_filename: `${id}.txt`,
      latest_version_id: `${id}-v1`,
      versions: [
        {
          ...FIXTURE_DOCUMENT.versions[0],
          id: `${id}-v1`,
          document_id: id,
          status,
        },
      ],
    };
  }

  it("renders zeroed metrics with an empty document list", () => {
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const summary = screen.getByLabelText(/Pipeline status summary/i);
    // All three metric tiles read 0 — no documents in any state.
    expect(summary.textContent ?? "").toMatch(/Review\s*0/);
    expect(summary.textContent ?? "").toMatch(/Failed\s*0/);
    expect(summary.textContent ?? "").toMatch(/Duplicate\s*0/);
  });

  it("counts NEEDS_REVIEW, FAILED, and DUPLICATE_DETECTED separately", () => {
    const docs: ApiDocument[] = [
      makeDoc("a", "NEEDS_REVIEW"),
      makeDoc("b", "NEEDS_REVIEW"),
      makeDoc("c", "FAILED"),
      makeDoc("d", "DUPLICATE_DETECTED"),
      makeDoc("e", "DUPLICATE_DETECTED"),
      makeDoc("f", "DUPLICATE_DETECTED"),
      // STORED and VALIDATED must not contribute to any of the three.
      makeDoc("g", "STORED"),
      makeDoc("h", "VALIDATED"),
    ];
    render(
      <PipelineWidget
        documents={docs}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const summary = screen.getByLabelText(/Pipeline status summary/i);
    expect(summary.textContent ?? "").toMatch(/Review\s*2/);
    expect(summary.textContent ?? "").toMatch(/Failed\s*1/);
    expect(summary.textContent ?? "").toMatch(/Duplicate\s*3/);
  });
});

describe("PipelineWidget — document row selection", () => {
  it("clicking a document row calls onSelectDocument with that id", () => {
    const docB: ApiDocument = {
      ...FIXTURE_DOCUMENT,
      id: "doc-002",
      original_filename: "second.txt",
      latest_version_id: "ver-002",
      versions: [
        {
          ...FIXTURE_DOCUMENT.versions[0],
          id: "ver-002",
          document_id: "doc-002",
        },
      ],
    };
    const onSelectDocument = vi.fn();
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT, docB]}
        selectedDocumentId="doc-001"
        onSelectDocument={onSelectDocument}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /second\.txt/i }));
    expect(onSelectDocument).toHaveBeenCalledWith("doc-002");
  });

  it("the selected row exposes aria-pressed=true and others aria-pressed=false", () => {
    const docB: ApiDocument = {
      ...FIXTURE_DOCUMENT,
      id: "doc-002",
      original_filename: "second.txt",
      latest_version_id: "ver-002",
      versions: [
        {
          ...FIXTURE_DOCUMENT.versions[0],
          id: "ver-002",
          document_id: "doc-002",
        },
      ],
    };
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT, docB]}
        selectedDocumentId="doc-002"
        onSelectDocument={() => {}}
      />,
    );

    expect(
      screen.getByRole("button", { name: /second\.txt/i }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: /test\.txt/i }),
    ).toHaveAttribute("aria-pressed", "false");
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
