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
  archived_at: null,
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
  // Single-scope default: the backend auto-fills personal:<user> when
  // the upload route receives no scope_kind/scope_ref query params.
  scopes: [
    {
      kind: "personal",
      ref: "user-1",
      added_at: "2026-05-01T00:00:00Z",
      added_by: "user-1",
      removed_at: null,
    },
  ],
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

  it("renders the API remediation hint when the server supplies one (#97)", async () => {
    // The new public error envelope (issue #97) carries `error.remediation`
    // — the frontend's notice banner surfaces it under the message.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_UPLOAD_UNSUPPORTED_TYPE",
            message: "Content type 'application/octet-stream' is not allowed.",
            status: 415,
            retryable: false,
            remediation:
              "Re-upload the file with one of the allowed content types, or ask an operator to widen the KW_ALLOWED_CONTENT_TYPES allowlist.",
          },
          detail: "Content type 'application/octet-stream' is not allowed.",
        },
        415,
      ),
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
    expect(
      screen.getByText(/Content type .* is not allowed\./),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/KW_ALLOWED_CONTENT_TYPES allowlist/),
    ).toBeInTheDocument();
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
    expect(screen.getByText(/The selected file is empty\./i)).toBeInTheDocument();
    // The remediation copy is also surfaced for the user.
    expect(
      screen.getByText(/non-empty file and try again/i),
    ).toBeInTheDocument();
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

describe("PipelineWidget — batch upload (#82)", () => {
  afterEach(() => vi.restoreAllMocks());

  const BATCH_REPORT = {
    summary: {
      total: 3,
      uploaded: 2,
      duplicate: 0,
      failed: 1,
      empty: 0,
      too_large: 0,
      rejected_content_type: 0,
    },
    results: [
      {
        filename: "a.txt",
        status: "uploaded",
        document_id: "doc-A",
        version_id: "ver-A",
        sha256: "a".repeat(64),
        bytes: 10,
        content_type: "text/plain",
        error_code: null,
        error_message: null,
      },
      {
        filename: "b.txt",
        status: "uploaded",
        document_id: "doc-B",
        version_id: "ver-B",
        sha256: "b".repeat(64),
        bytes: 12,
        content_type: "text/plain",
        error_code: null,
        error_message: null,
      },
      {
        filename: "broken.bin",
        status: "rejected_content_type",
        document_id: null,
        version_id: null,
        sha256: null,
        bytes: 4,
        content_type: "application/octet-stream",
        error_code: "KW_UPLOAD_UNSUPPORTED_TYPE",
        error_message: "Content type 'application/octet-stream' is not allowed.",
      },
    ],
  } as const;

  it("dispatches to /documents/upload/batch when multiple files are selected", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse(BATCH_REPORT));
    const onUploaded = vi.fn();
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        onUploaded={onUploaded}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(["aa"], "a.txt", { type: "text/plain" }),
      new File(["bb"], "b.txt", { type: "text/plain" }),
      new File(["cc"], "broken.bin", { type: "application/octet-stream" }),
    ];
    fireEvent.change(input, { target: { files } });

    await waitFor(() => {
      expect(screen.getByTestId("batch-upload-report")).toBeInTheDocument();
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(urlOf(fetchSpy.mock.calls[0][0])).toContain("/documents/upload/batch");

    // Per-file rows render with the per-outcome status.
    const rows = screen.getAllByTestId("batch-upload-report-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("a.txt");
    expect(rows[2]).toHaveTextContent("rejected_content_type");
    expect(rows[2]).toHaveTextContent(
      /Content type .* is not allowed\./,
    );

    // Aggregate banner text.
    expect(screen.getByText(/2\/3 new/)).toBeInTheDocument();

    // First successful upload's document_id is bubbled up so the
    // catalog can refocus.
    expect(onUploaded).toHaveBeenCalledWith("doc-A");
  });

  it("uses the single-file endpoint when exactly one file is selected", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse(UPLOAD_RESPONSE));
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["aa"], "a.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });
    const url = urlOf(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("/documents/upload");
    expect(url).not.toContain("/documents/upload/batch");
    expect(screen.queryByTestId("batch-upload-report")).toBeNull();
  });

  it("renders an inline alert if the batch endpoint itself returns a non-200", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "No files attached." }, 400),
    );
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(["aa"], "a.txt", { type: "text/plain" }),
      new File(["bb"], "b.txt", { type: "text/plain" }),
    ];
    fireEvent.change(input, { target: { files } });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/No files attached\./)).toBeInTheDocument();
    expect(screen.queryByTestId("batch-upload-report")).toBeNull();
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

  it("the selected row exposes aria-current=page and others have no aria-current", () => {
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
    ).toHaveAttribute("aria-current", "page");
    // Non-selected rows should not carry the attribute at all (rather
    // than aria-current="false") — the latter has different semantics.
    expect(
      screen.getByRole("button", { name: /test\.txt/i }),
    ).not.toHaveAttribute("aria-current");
  });
});

describe("PipelineWidget — catalog filter bar (#86)", () => {
  it("does not render the filter bar when no filter prop is provided", () => {
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId={FIXTURE_DOCUMENT.id}
        onSelectDocument={() => {}}
      />,
    );

    expect(screen.queryByLabelText(/Filter documents/i)).not.toBeInTheDocument();
  });

  it("renders search input and saved-view chips when filter prop is provided", () => {
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId={FIXTURE_DOCUMENT.id}
        onSelectDocument={() => {}}
        filter={{ status: [], q: "" }}
        onFilterChange={() => {}}
      />,
    );

    expect(screen.getByLabelText(/Search by filename/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Review/i })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.getByRole("tab", { name: /Validated/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Failed/i })).toBeInTheDocument();
  });

  it("clicking a saved-view chip emits the matching status set", () => {
    const onFilterChange = vi.fn();
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId={FIXTURE_DOCUMENT.id}
        onSelectDocument={() => {}}
        filter={{ status: [], q: "" }}
        onFilterChange={onFilterChange}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: /Review/i }));
    expect(onFilterChange).toHaveBeenCalledWith({
      status: ["NEEDS_REVIEW", "DUPLICATE_DETECTED"],
      q: "",
    });
  });

  it("re-clicking the active saved-view chip clears the status filter", () => {
    const onFilterChange = vi.fn();
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId={FIXTURE_DOCUMENT.id}
        onSelectDocument={() => {}}
        filter={{ status: ["NEEDS_REVIEW", "DUPLICATE_DETECTED"], q: "" }}
        onFilterChange={onFilterChange}
      />,
    );

    const reviewTab = screen.getByRole("tab", { name: /Review/i });
    expect(reviewTab).toHaveAttribute("aria-selected", "true");
    fireEvent.click(reviewTab);
    expect(onFilterChange).toHaveBeenCalledWith({ status: [], q: "" });
  });

  it("typing in the search box updates the q filter", () => {
    const onFilterChange = vi.fn();
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId={FIXTURE_DOCUMENT.id}
        onSelectDocument={() => {}}
        filter={{ status: [], q: "" }}
        onFilterChange={onFilterChange}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Search by filename/i), {
      target: { value: "policy" },
    });
    expect(onFilterChange).toHaveBeenCalledWith({ status: [], q: "policy" });
  });

  it("Clear button appears when a filter is active and resets both axes", () => {
    const onFilterChange = vi.fn();
    render(
      <PipelineWidget
        documents={[FIXTURE_DOCUMENT]}
        selectedDocumentId={FIXTURE_DOCUMENT.id}
        onSelectDocument={() => {}}
        filter={{ status: ["FAILED", "REJECTED"], q: "policy" }}
        onFilterChange={onFilterChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Clear all filters/i }));
    expect(onFilterChange).toHaveBeenCalledWith({ status: [], q: "" });
  });

  it("shows a 'no match' empty state when the filtered list is empty", () => {
    render(
      <PipelineWidget
        documents={[]}
        selectedDocumentId=""
        onSelectDocument={() => {}}
        filter={{ status: ["FAILED"], q: "" }}
        onFilterChange={() => {}}
      />,
    );

    expect(screen.getByText(/No documents match this filter/i)).toBeInTheDocument();
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
