/**
 * Scope picker mockup tests (EPIC-D #218).
 *
 * The Swym scope dropdown is a pure UX preview — it must NOT alter
 * the upload wire contract. These tests pin three things:
 *
 * 1. Default state — Personal selected, Swym disabled.
 * 2. Selection — Personal stays selectable, Swym is rejected.
 * 3. Wire contract — submit() still posts only `file`, no `scope_kind`,
 *    no `scope_ref`. If a future change accidentally promotes the
 *    mockup to a real field on the request, this test breaks.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UploadQueue } from "../UploadQueue";

// Stub the api/client module before the component module is imported.
// We replace ``uploadDocumentWithProgress`` with a vi.fn() so the
// assertion can read the exact payload the queue submits.
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>(
    "../../api/client",
  );
  return {
    ...actual,
    uploadDocumentWithProgress: vi.fn(),
  };
});

import { uploadDocumentWithProgress } from "../../api/client";

const mockedUpload = uploadDocumentWithProgress as unknown as ReturnType<
  typeof vi.fn
>;

afterEach(() => {
  vi.clearAllMocks();
});

describe("UploadQueue — scope picker mockup", () => {
  it("defaults to Personal workspace and disables the Swym option", () => {
    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);

    const select = screen.getByTestId(
      "kw-upload-scope-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("personal");
    // Sanity check: label is wired to the select via htmlFor.
    const label = document.querySelector(
      "label[for='kw-upload-scope']",
    ) as HTMLLabelElement;
    expect(label).toBeTruthy();
    expect(label.textContent).toMatch(/Destination/i);

    const swymOption = Array.from(select.options).find(
      (opt) => opt.value === "swym_community",
    );
    expect(swymOption).toBeDefined();
    expect(swymOption!.disabled).toBe(true);
    // The "Coming soon" pill is the visual cue beside the dropdown.
    const pill = document.querySelector(".kw-scope__pill");
    expect(pill).toBeTruthy();
    expect(pill!.textContent).toMatch(/Coming soon/i);
    // The future-of-feature banner is rendered.
    expect(
      screen.getByTestId("kw-upload-scope-banner"),
    ).toBeInTheDocument();
  });

  it("re-selecting Personal is a no-op (state stays personal)", () => {
    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);

    const select = screen.getByTestId(
      "kw-upload-scope-select",
    ) as HTMLSelectElement;

    // Drive the change handler with the same value — value should not
    // bounce, no re-render artefact, no console errors.
    fireEvent.change(select, { target: { value: "personal" } });
    expect(select.value).toBe("personal");
  });

  it("cannot programmatically select the disabled Swym option", () => {
    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);

    const select = screen.getByTestId(
      "kw-upload-scope-select",
    ) as HTMLSelectElement;

    // Even if a synthetic event ships ``swym_community`` the handler
    // ignores it. The select stays on personal — verifying both the
    // visual state and the underlying React state.
    fireEvent.change(select, { target: { value: "swym_community" } });
    expect(select.value).toBe("personal");
  });

  it("submit still posts only `file` — no scope_kind, no scope_ref on the wire", async () => {
    mockedUpload.mockResolvedValue({
      id: "ver-1",
      document_id: "doc-1",
      version_number: 1,
      filename: "x.txt",
      content_type: "text/plain",
      file_size: 4,
      sha256: "x".repeat(64),
      storage_uri: "file://x",
      status: "STORED",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-01T00:00:00Z",
    });

    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);

    const file = new File(["body"], "doc.txt", { type: "text/plain" });
    const fileInput = document.querySelector(
      "input[type='file']:not([multiple]):not([webkitdirectory])",
    ) as HTMLInputElement;
    expect(fileInput).toBeTruthy();

    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: (i: number) => (i === 0 ? file : null) },
      configurable: true,
    });
    fireEvent.change(fileInput);

    await waitFor(() => {
      expect(mockedUpload).toHaveBeenCalledTimes(1);
    });

    // The wire contract: first arg is the File, second is an options
    // bag with baseUrl + onProgress only. NO scope_kind, NO scope_ref,
    // NO `scope` property of any kind. The mockup cannot leak into the
    // request.
    const [submittedFile, submittedOpts] = mockedUpload.mock.calls[0];
    expect(submittedFile).toBe(file);
    expect(submittedOpts).toBeDefined();
    expect(Object.keys(submittedOpts)).toEqual(
      expect.arrayContaining(["baseUrl", "onProgress"]),
    );
    // Defensive: assert the absence of the future-contract fields.
    expect(submittedOpts.scope_kind).toBeUndefined();
    expect(submittedOpts.scope_ref).toBeUndefined();
    expect(submittedOpts.scope).toBeUndefined();
  });
});
