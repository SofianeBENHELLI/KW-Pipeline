/**
 * #292 — pre-import duplicate check.
 *
 * Forge hashes a picked file locally and asks
 * ``GET /documents/by-hash/{sha256}`` whether the digest is already
 * known. On a hit the row is flagged ``DUPLICATE_DETECTED`` and the
 * bytes are not imported; on a miss the row drops into the regular queue.
 * These tests stub the client module so the
 * UI flow is exercised hermetically — no real fetch, no Web Crypto.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>(
    "../../api/client",
  );
  return {
    ...actual,
    uploadDocumentWithProgress: vi.fn(),
    checkDocumentHash: vi.fn(),
    hashFileSha256: vi.fn(),
  };
});

import {
  checkDocumentHash,
  hashFileSha256,
  uploadDocumentWithProgress,
} from "../../api/client";
import { UploadQueue } from "../UploadQueue";

const mockedUpload = vi.mocked(uploadDocumentWithProgress);
const mockedHashCheck = vi.mocked(checkDocumentHash);
const mockedHashFile = vi.mocked(hashFileSha256);

afterEach(() => {
  vi.clearAllMocks();
});

function pick(file: File) {
  const fileInput = document.querySelector(
    "input[type='file']:not([multiple]):not([webkitdirectory])",
  ) as HTMLInputElement;
  Object.defineProperty(fileInput, "files", {
    value: { 0: file, length: 1, item: (i: number) => (i === 0 ? file : null) },
    configurable: true,
  });
  fireEvent.change(fileInput);
}

describe("UploadQueue — pre-import duplicate check (#292)", () => {
  it("flags a precheck-hit row as DUPLICATE_DETECTED before uploading bytes", async () => {
    const digest = "a".repeat(64);
    mockedHashFile.mockResolvedValue(digest);
    mockedHashCheck.mockResolvedValue({
      exists: true,
      sha256: digest,
      document_id: "doc-existing",
      version_id: "ver-existing",
      version_number: 1,
      original_filename: "first.txt",
    });

    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);
    pick(new File(["body"], "doc.txt", { type: "text/plain" }));

    // The row surfaces the duplicate banner and does not offer a bypass.
    expect(
      await screen.findByTestId("kw-queue-duplicate"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /upload anyway/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Duplicate flagged; not imported/i)).toBeInTheDocument();
    // Critically: no upload was started.
    expect(mockedUpload).not.toHaveBeenCalled();
  });

  it("leaves duplicate bytes in the browser — no upload call ever", async () => {
    const digest = "b".repeat(64);
    mockedHashFile.mockResolvedValue(digest);
    mockedHashCheck.mockResolvedValue({
      exists: true,
      sha256: digest,
      document_id: "doc-1",
      version_id: "ver-1",
      version_number: 2,
      original_filename: "x.txt",
    });

    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);
    pick(new File(["body"], "doc.txt", { type: "text/plain" }));

    await screen.findByTestId("kw-queue-duplicate");
    expect(mockedUpload).not.toHaveBeenCalled();
  });

  it("non-duplicate file goes straight through to upload", async () => {
    const digest = "d".repeat(64);
    mockedHashFile.mockResolvedValue(digest);
    mockedHashCheck.mockResolvedValue({
      exists: false,
      sha256: digest,
      document_id: null,
      version_id: null,
      version_number: null,
      original_filename: null,
    });
    mockedUpload.mockResolvedValue({
      id: "ver-1",
      document_id: "doc-1",
      version_number: 1,
      filename: "doc.txt",
      content_type: "text/plain",
      file_size: 4,
      sha256: digest,
      storage_uri: "file://x",
      status: "STORED",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-01T00:00:00Z",
    });

    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);
    pick(new File(["body"], "doc.txt", { type: "text/plain" }));

    await waitFor(() => expect(mockedUpload).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("kw-queue-duplicate")).not.toBeInTheDocument();
  });

  it("precheck failure (network blip) falls back to legacy upload behaviour", async () => {
    mockedHashFile.mockResolvedValue("e".repeat(64));
    mockedHashCheck.mockRejectedValue(new Error("offline"));
    mockedUpload.mockResolvedValue({
      id: "ver-1",
      document_id: "doc-1",
      version_number: 1,
      filename: "doc.txt",
      content_type: "text/plain",
      file_size: 4,
      sha256: "e".repeat(64),
      storage_uri: "file://x",
      status: "STORED",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-01T00:00:00Z",
    });

    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);
    pick(new File(["body"], "doc.txt", { type: "text/plain" }));

    // Even with the precheck broken, upload still happens — backend
    // remains the source of truth for the duplicate flag.
    await waitFor(() => expect(mockedUpload).toHaveBeenCalledTimes(1));
  });
});
