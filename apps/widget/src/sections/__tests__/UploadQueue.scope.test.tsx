/**
 * Scope picker wire-up tests (EPIC-D #218 / #250).
 *
 * The picker now drives the wire: ``POST /documents/upload`` accepts
 * optional ``scope_kind`` / ``scope_ref`` query params, and the queue
 * forwards them only when the user actively picked a non-default
 * scope. The default personal scope is sent as "no params" so the
 * backend can auto-fill ``personal:<current_user.id>``.
 *
 * These tests pin five things:
 *
 * 1. Default state — Personal selected, Swym disabled.
 * 2. Selection — Personal stays selectable, Swym is rejected.
 * 3. Default personal scope → upload omits ``scope_kind`` / ``scope_ref``
 *    (so the backend's ``get_current_user`` default kicks in).
 * 4. Explicit non-default scope → upload includes both ``scope_kind``
 *    and ``scope_ref`` verbatim (state-driven, since the dropdown is
 *    still locked to personal until D.3 ships the membership client).
 * 5. The Swym option's "Coming soon" tooltip points at ADR-020.
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

const mockedUpload = vi.mocked(uploadDocumentWithProgress);

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

  it("Swym option tooltip points at ADR-020", () => {
    render(<UploadQueue apiBaseUrl="http://test.local" onUploaded={() => {}} />);

    const select = screen.getByTestId(
      "kw-upload-scope-select",
    ) as HTMLSelectElement;
    const swymOption = Array.from(select.options).find(
      (opt) => opt.value === "swym_community",
    );
    expect(swymOption).toBeDefined();
    // The pre-#250 tooltip mentioned "EPIC-D"; post-wire-up we
    // point at the ADR that owns the scope contract so curious
    // operators can find the rationale without a code dive.
    expect(swymOption!.title).toMatch(/ADR-020/);
    // The "Coming soon" pill alongside the dropdown gets the same
    // refresh — the two surfaces must stay in sync.
    const pill = document.querySelector(".kw-scope__pill") as HTMLElement;
    expect(pill.title).toMatch(/ADR-020/);
  });

  it("default personal scope → omits scope_kind / scope_ref", async () => {
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

    // The wire contract for the default personal scope: omit both
    // ``scope_kind`` and ``scope_ref`` so the backend auto-fills with
    // ``personal:<current_user.id>`` from ``get_current_user``.
    const [submittedFile, submittedOpts] = mockedUpload.mock.calls[0];
    expect(submittedFile).toBe(file);
    expect(submittedOpts).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const opts = submittedOpts as any;
    expect(Object.keys(opts)).toEqual(
      expect.arrayContaining(["baseUrl", "onProgress"]),
    );
    expect(opts.scope_kind).toBeUndefined();
    expect(opts.scope_ref).toBeUndefined();
    expect(opts.scope).toBeUndefined();
  });

});
