import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  extractVersion,
  getDocument,
  getExtraction,
  getMarkdown,
  getSemantic,
  listDocuments,
  rejectVersion,
  uploadDocument,
  validateVersion,
} from "./client";
import type {
  ApiDocument,
  ApiRawExtraction,
  ApiSemanticDocument,
  ListDocumentsResponse,
} from "./types";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeTextResponse(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/markdown" },
  });
}

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const FIXTURE_VERSION = {
  id: "ver-001",
  document_id: "doc-001",
  version_number: 1,
  filename: "test.txt",
  content_type: "text/plain",
  file_size: 100,
  sha256: "abc123",
  storage_uri: "file://test",
  status: "STORED" as const,
  duplicate_of_version_id: null,
  failure_reason: null,
  reviewer_note: null,
  reviewed_at: null,
  created_at: "2026-05-01T00:00:00Z",
};

const FIXTURE_DOCUMENT: ApiDocument = {
  id: "doc-001",
  original_filename: "test.txt",
  latest_version_id: "ver-001",
  created_at: "2026-05-01T00:00:00Z",
  versions: [FIXTURE_VERSION],
};

const FIXTURE_LIST: ListDocumentsResponse = {
  items: [FIXTURE_DOCUMENT],
  next_cursor: null,
};

const FIXTURE_EXTRACTION: ApiRawExtraction = {
  id: "ext-001",
  document_version_id: "ver-001",
  parser_name: "PlainTextParser",
  parser_version: "1.0",
  text: "Hello world",
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
    title: "Test Document",
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
  markdown: "# Test Document\n\nHello world",
  created_at: "2026-05-01T00:00:00Z",
};

// ─── Tests ───────────────────────────────────────────────────────────────────

// `openapi-fetch` invokes `fetch` with a Request object (not a URL string),
// so tests read the URL/method/body off the Request.
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

describe("API client — happy paths", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);

        if (url.match(/\/documents\?/)) {
          return Promise.resolve(makeJsonResponse(FIXTURE_LIST));
        }
        if (url.match(/\/documents\/doc-001\/versions\/ver-001\/extraction$/)) {
          return Promise.resolve(makeJsonResponse(FIXTURE_EXTRACTION));
        }
        if (url.match(/\/documents\/doc-001\/versions\/ver-001\/semantic$/)) {
          return Promise.resolve(makeJsonResponse(FIXTURE_SEMANTIC));
        }
        if (url.match(/\/documents\/doc-001\/versions\/ver-001\/markdown$/)) {
          return Promise.resolve(makeTextResponse("# Test\n"));
        }
        if (url.match(/\/documents\/doc-001\/versions\/ver-001\/extract$/)) {
          return Promise.resolve(makeJsonResponse(FIXTURE_EXTRACTION));
        }
        if (url.match(/\/documents\/doc-001\/versions\/ver-001\/validate$/)) {
          return Promise.resolve(makeJsonResponse({ ...FIXTURE_SEMANTIC, validation_status: "validated" }));
        }
        if (url.match(/\/documents\/doc-001\/versions\/ver-001\/reject$/)) {
          return Promise.resolve(makeJsonResponse({ ...FIXTURE_SEMANTIC, validation_status: "rejected" }));
        }
        if (url.match(/\/documents\/doc-001$/)) {
          return Promise.resolve(makeJsonResponse(FIXTURE_DOCUMENT));
        }
        if (url.match(/\/documents\/upload$/)) {
          return Promise.resolve(makeJsonResponse(FIXTURE_VERSION));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("listDocuments returns paginated items", async () => {
    const result = await listDocuments();
    expect(result.items).toHaveLength(1);
    expect(result.items[0].id).toBe("doc-001");
    expect(result.next_cursor).toBeNull();
  });

  it("listDocuments forwards limit and cursor as query params", async () => {
    await listDocuments({ limit: 10, cursor: "token123" });
    const [input] = vi.mocked(fetch).mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    const url = urlOf(input);
    expect(url).toContain("limit=10");
    expect(url).toContain("cursor=token123");
  });

  it("listDocuments forwards status filters as repeatable query params", async () => {
    await listDocuments({ status: ["NEEDS_REVIEW", "FAILED"] });
    const [input] = vi.mocked(fetch).mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    const url = urlOf(input);
    expect(url).toContain("status=NEEDS_REVIEW");
    expect(url).toContain("status=FAILED");
  });

  it("listDocuments forwards trimmed q as a query param, dropping empties", async () => {
    await listDocuments({ q: "  procurement  " });
    let [input] = vi.mocked(fetch).mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    expect(urlOf(input)).toContain("q=procurement");

    vi.mocked(fetch).mockClear();
    await listDocuments({ q: "   " });
    [input] = vi.mocked(fetch).mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    expect(urlOf(input)).not.toContain("q=");
  });

  it("getDocument returns a full document", async () => {
    const doc = await getDocument("doc-001");
    expect(doc.id).toBe("doc-001");
    expect(doc.versions).toHaveLength(1);
  });

  it("getExtraction returns raw extraction", async () => {
    const ext = await getExtraction("doc-001", "ver-001");
    expect(ext.text).toBe("Hello world");
    expect(ext.parser_name).toBe("PlainTextParser");
  });

  it("extractVersion triggers extraction via POST", async () => {
    const ext = await extractVersion("doc-001", "ver-001");
    expect(ext.id).toBe("ext-001");
    const [input] = vi.mocked(fetch).mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    expect((input as Request).method).toBe("POST");
  });

  it("getSemantic returns semantic document", async () => {
    const sem = await getSemantic("doc-001", "ver-001");
    expect(sem.validation_status).toBe("needs_review");
    expect(sem.document_profile.title).toBe("Test Document");
  });

  it("getMarkdown returns markdown text", async () => {
    const md = await getMarkdown("doc-001", "ver-001");
    expect(md).toBe("# Test\n");
  });

  it("validateVersion sends POST and returns updated semantic", async () => {
    const sem = await validateVersion("doc-001", "ver-001", "LGTM");
    expect(sem.validation_status).toBe("validated");
    const [input] = vi.mocked(fetch).mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    const req = input as Request;
    expect(req.method).toBe("POST");
    expect(await req.clone().json()).toEqual({ reviewer_note: "LGTM" });
  });

  it("rejectVersion sends POST and returns updated semantic", async () => {
    const sem = await rejectVersion("doc-001", "ver-001");
    expect(sem.validation_status).toBe("rejected");
    const [input] = vi.mocked(fetch).mock.calls[0] as [RequestInfo | URL, ...unknown[]];
    const req = input as Request;
    expect(req.method).toBe("POST");
    expect(await req.clone().json()).toEqual({ reviewer_note: null });
  });

  it("uploadDocument sends multipart form data", async () => {
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const version = await uploadDocument(file);
    expect(version.id).toBe("ver-001");
    // uploadDocument bypasses openapi-fetch (multipart), calling fetch
    // directly with (url, init).
    const [, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
  });
});

describe("API client — error paths", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("throws ApiError with status and detail on non-OK JSON response", async () => {
    // Use mockImplementation so each call gets a fresh Response — Response
    // bodies are single-use streams and mockResolvedValue would reuse the
    // same already-consumed body on the second call.
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ detail: "Document not found." }, 404)),
    );
    await expect(getDocument("missing")).rejects.toBeInstanceOf(ApiError);
    try {
      await getDocument("missing");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(404);
      expect(apiErr.detail).toBe("Document not found.");
    }
  });

  it("uses statusText as fallback when response body is not JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Internal Server Error", { status: 500, statusText: "Internal Server Error" }),
    );
    await expect(listDocuments()).rejects.toMatchObject({
      status: 500,
      detail: "Internal Server Error",
    });
  });

  it("propagates network errors as-is", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(listDocuments()).rejects.toThrow("Failed to fetch");
  });

  it("getVersion rejects with a clear not-implemented message", async () => {
    const { getVersion } = await import("./client");
    await expect(getVersion("doc-001", "ver-001")).rejects.toThrow(/not yet implemented/i);
  });

  it("ApiError carries code/retryable/remediation from the public envelope (#97)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        makeJsonResponse(
          {
            error: {
              code: "KW_LIFECYCLE_CONFLICT",
              message: "Version is in STORED, not NEEDS_REVIEW.",
              status: 409,
              retryable: false,
              remediation: "Refresh the document and re-evaluate the available actions.",
            },
            detail: "Version is in STORED, not NEEDS_REVIEW.",
          },
          409,
        ),
      ),
    );

    try {
      await getDocument("doc-001");
      throw new Error("expected ApiError");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(409);
      expect(apiErr.code).toBe("KW_LIFECYCLE_CONFLICT");
      expect(apiErr.retryable).toBe(false);
      expect(apiErr.remediation).toMatch(/Refresh the document/);
      // `detail` falls back to the envelope's `message` when present.
      expect(apiErr.detail).toBe("Version is in STORED, not NEEDS_REVIEW.");
    }
  });

  it("ApiError defaults code/retryable/remediation when envelope is absent", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("plain text body", { status: 502, statusText: "Bad Gateway" }),
    );
    try {
      await listDocuments();
      throw new Error("expected ApiError");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(502);
      expect(apiErr.code).toBe("KW_HTTP_ERROR");
      expect(apiErr.retryable).toBe(false);
      expect(apiErr.remediation).toBeNull();
    }
  });
});
