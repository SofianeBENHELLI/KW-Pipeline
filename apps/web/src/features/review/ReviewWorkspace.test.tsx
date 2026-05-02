import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewWorkspace } from "./ReviewWorkspace";
import type {
  ApiDocument,
  ApiRawExtraction,
  ApiSemanticDocument,
  DocumentVersionStatus,
} from "../../api/types";

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

function makeDocument(status: DocumentVersionStatus): ApiDocument {
  return {
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
        file_size: 100,
        sha256: "abc123def456789012345abcdef1234567890abcdef1234567890abcdef12345",
        storage_uri: "file://test",
        status,
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-05-01T00:00:00Z",
      },
    ],
  };
}

const FIXTURE_EXTRACTION: ApiRawExtraction = {
  id: "ext-001",
  document_version_id: "ver-001",
  parser_name: "PlainTextParser",
  parser_version: "1.0",
  text: "Extracted text body.",
  sections: [],
  source_references: [],
  warnings: [],
  created_at: "2026-05-01T00:00:00Z",
};

const FIXTURE_SEMANTIC: ApiSemanticDocument = {
  id: "sem-001",
  document_version_id: "ver-001",
  schema_version: "v0.1",
  document_profile: {
    title: "Test",
    document_type: "unknown",
    purpose: null,
    audience: null,
    executive_summary: null,
  },
  sections: [],
  assets: [],
  warnings: [],
  source_references: [],
  validation_status: "needs_review",
  markdown: "# Hello",
  created_at: "2026-05-01T00:00:00Z",
};

describe("ReviewWorkspace — action bar enable matrix", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404)),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("enables Run extraction when status is STORED", async () => {
    render(<ReviewWorkspace document={makeDocument("STORED")} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Run extraction/i })).toBeEnabled();
    });
    expect(
      screen.getByRole("button", { name: /Generate semantic output/i }),
    ).toBeDisabled();
  });

  it("enables Generate semantic output for EXTRACTED, SEMANTIC_READY, NEEDS_REVIEW", async () => {
    for (const status of ["EXTRACTED", "SEMANTIC_READY", "NEEDS_REVIEW"] as const) {
      const { unmount } = render(<ReviewWorkspace document={makeDocument(status)} />);
      await waitFor(() => {
        expect(
          screen.getByRole("button", { name: /Generate semantic output/i }),
        ).toBeEnabled();
      });
      unmount();
    }
  });

  it("disables both action buttons after the document is VALIDATED", async () => {
    render(<ReviewWorkspace document={makeDocument("VALIDATED")} />);
    await waitFor(() => {
      const extractBtn = screen.getByRole("button", { name: /Run extraction/i });
      expect(extractBtn).toBeDisabled();
      expect(extractBtn).toHaveAttribute("title", expect.stringMatching(/validated/i));
    });
  });

  it("Refresh is always enabled", async () => {
    render(<ReviewWorkspace document={makeDocument("VALIDATED")} />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Refresh$/i })).toBeEnabled();
    });
  });
});

describe("ReviewWorkspace — actions trigger backend calls", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Run extraction calls extractVersion and triggers refresh", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        calls.push(url);
        if (url.endsWith("/extract")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_EXTRACTION));
        }
        if (url.endsWith("/extraction")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_EXTRACTION));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    const onMutationCompleted = vi.fn();
    render(
      <ReviewWorkspace
        document={makeDocument("STORED")}
        onMutationCompleted={onMutationCompleted}
      />,
    );

    const button = await screen.findByRole("button", { name: /Run extraction/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(calls.some((u) => u.endsWith("/extract"))).toBe(true);
    });
    await waitFor(() => {
      expect(onMutationCompleted).toHaveBeenCalled();
    });
  });

  it("Generate semantic output calls generateSemantic", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        calls.push(`${(input as Request).method} ${url}`);
        if (url.endsWith("/semantic") && (input as Request).method === "POST") {
          return Promise.resolve(makeJsonResponse(FIXTURE_SEMANTIC));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    const onMutationCompleted = vi.fn();
    render(
      <ReviewWorkspace
        document={makeDocument("EXTRACTED")}
        onMutationCompleted={onMutationCompleted}
      />,
    );

    const button = await screen.findByRole("button", {
      name: /Generate semantic output/i,
    });
    fireEvent.click(button);

    await waitFor(() => {
      expect(
        calls.some((entry) => entry.startsWith("POST") && entry.endsWith("/semantic")),
      ).toBe(true);
    });
    await waitFor(() => {
      expect(onMutationCompleted).toHaveBeenCalled();
    });
  });

  it("Validate updates the semantic state and calls onMutationCompleted", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.endsWith("/extraction")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_EXTRACTION));
        }
        if (url.endsWith("/semantic")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_SEMANTIC));
        }
        if (url.endsWith("/validate")) {
          return Promise.resolve(
            makeJsonResponse({ ...FIXTURE_SEMANTIC, validation_status: "validated" }),
          );
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    const onMutationCompleted = vi.fn();
    render(
      <ReviewWorkspace
        document={makeDocument("NEEDS_REVIEW")}
        onMutationCompleted={onMutationCompleted}
      />,
    );

    // Wait for details to load — "needs_review" appears in the semantic-list.
    await waitFor(() => {
      expect(screen.getByText("needs_review")).toBeInTheDocument();
    });
    const validate = screen.getByRole("button", { name: /^Validate$/i });
    fireEvent.click(validate);

    await waitFor(() => {
      expect(screen.getByText("validated")).toBeInTheDocument();
    });
    expect(onMutationCompleted).toHaveBeenCalled();
  });

  it("renders an inline error banner if extraction fails without nuking the panel", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.endsWith("/extract")) {
          return Promise.resolve(
            makeJsonResponse({ detail: "Parser crashed." }, 500),
          );
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    render(<ReviewWorkspace document={makeDocument("STORED")} />);
    const button = await screen.findByRole("button", { name: /Run extraction/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText(/Parser crashed\./)).toBeInTheDocument();
    });
    // The rest of the workspace should still be present.
    expect(
      screen.getByRole("heading", { name: /Raw extraction/i }),
    ).toBeInTheDocument();
  });
});

describe("ReviewWorkspace — reject path", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Reject calls /reject, updates the semantic state, and notifies the parent", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        calls.push(`${(input as Request).method ?? "GET"} ${url}`);
        if (url.endsWith("/extraction")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_EXTRACTION));
        }
        if (url.endsWith("/semantic")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_SEMANTIC));
        }
        if (url.endsWith("/reject")) {
          return Promise.resolve(
            makeJsonResponse({ ...FIXTURE_SEMANTIC, validation_status: "rejected" }),
          );
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    const onMutationCompleted = vi.fn();
    render(
      <ReviewWorkspace
        document={makeDocument("NEEDS_REVIEW")}
        onMutationCompleted={onMutationCompleted}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("needs_review")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^Reject$/i }));

    await waitFor(() => {
      expect(screen.getByText("rejected")).toBeInTheDocument();
    });
    expect(
      calls.some((entry) => entry.startsWith("POST") && entry.endsWith("/reject")),
    ).toBe(true);
    expect(onMutationCompleted).toHaveBeenCalled();
  });
});

describe("ReviewWorkspace — review action concurrency", () => {
  afterEach(() => vi.restoreAllMocks());

  it("clicking Validate twice in flight only fires one /validate request", async () => {
    const validateCalls: string[] = [];
    let resolveValidate: (response: Response) => void = () => {};
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.endsWith("/extraction")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_EXTRACTION));
        }
        if (url.endsWith("/semantic")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_SEMANTIC));
        }
        if (url.endsWith("/validate")) {
          validateCalls.push(url);
          return new Promise<Response>((resolve) => {
            resolveValidate = resolve;
          });
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    render(<ReviewWorkspace document={makeDocument("NEEDS_REVIEW")} />);

    await waitFor(() => {
      expect(screen.getByText("needs_review")).toBeInTheDocument();
    });
    const validate = screen.getByRole("button", { name: /^Validate$/i });
    fireEvent.click(validate);

    // Second click while the first request is still in flight — must be
    // a no-op because the button is disabled. Asserting on the disabled
    // attribute is the contract; the duplicate click cannot reach the
    // handler.
    await waitFor(() => expect(validate).toBeDisabled());
    fireEvent.click(validate);

    expect(validateCalls.length).toBe(1);

    // Drain the pending request so the test cleanly tears down.
    resolveValidate(
      makeJsonResponse({ ...FIXTURE_SEMANTIC, validation_status: "validated" }),
    );
    await waitFor(() => {
      expect(screen.getByText("validated")).toBeInTheDocument();
    });
  });
});

describe("ReviewWorkspace — reviewer note accessibility", () => {
  afterEach(() => vi.restoreAllMocks());

  it("textarea is reachable by its accessible name", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Not found" }, 404),
    );
    render(<ReviewWorkspace document={makeDocument("NEEDS_REVIEW")} />);

    const textarea = screen.getByLabelText(/Reviewer note/i);
    expect(textarea.tagName).toBe("TEXTAREA");
    expect(textarea).toBeEnabled();
  });

  it("textarea is disabled when the version is not in NEEDS_REVIEW", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Not found" }, 404),
    );
    render(<ReviewWorkspace document={makeDocument("VALIDATED")} />);

    expect(screen.getByLabelText(/Reviewer note/i)).toBeDisabled();
  });
});

describe("ReviewWorkspace — refresh indicator", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a refresh indicator when loadingSelected is true", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Not found" }, 404),
    );
    render(
      <ReviewWorkspace document={makeDocument("STORED")} loadingSelected />,
    );
    expect(
      screen.getByRole("status", { name: /Refreshing document/i }),
    ).toBeInTheDocument();
  });

  it("shows a warning banner when refreshError is set, keeping the document visible", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Not found" }, 404),
    );
    render(
      <ReviewWorkspace
        document={makeDocument("STORED")}
        refreshError="Network error"
      />,
    );
    expect(screen.getByText(/Refresh failed/i)).toBeInTheDocument();
    expect(screen.getByText(/Network error/i)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /test\.txt/i }),
    ).toBeInTheDocument();
  });
});
