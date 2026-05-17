/**
 * TaxonomyModeBadge coverage (ADR-018 §PR #346).
 *
 * Pinned scenarios:
 * - 200 → pill renders with the correct state modifier + ``vN`` text.
 * - 503 → pill is absent (rail stays clean).
 * - Click → ``useNavigate`` is called with
 *   ``/admin/taxonomy?taxonomy_id=…``.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiTaxonomy } from "../../api/types";
import { TaxonomyModeBadge } from "./TaxonomyModeBadge";

// Capture the navigate spy at module scope so each test can read it.
const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeTaxonomy(overrides: Partial<ApiTaxonomy> = {}): ApiTaxonomy {
  return {
    schema_version: "v0.1",
    taxonomy_id: "tx-active",
    version_number: 4,
    version_label: "Q1 2026 launch",
    state: "VALIDATED_V1",
    taxonomy: { schema_version: "v0.1", categories: [] },
    suggestions: [],
    created_at: "2026-01-15T10:00:00Z",
    state_changed_at: "2026-01-15T10:00:00Z",
    created_by: "ada",
    superseded_version_number: null,
    ...overrides,
  } as ApiTaxonomy;
}

function renderBadge() {
  return render(
    <MemoryRouter>
      <TaxonomyModeBadge />
    </MemoryRouter>,
  );
}

describe("<TaxonomyModeBadge />", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    navigateMock.mockReset();
  });

  it("renders the pill with vN text and state modifier on a 200 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeTaxonomy()),
    );

    renderBadge();

    const badge = await screen.findByTestId("taxonomy-mode-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("v4");
    // StatePill from the admin view paints the modifier class for us.
    const pill = screen.getByTestId("state-pill-VALIDATED_V1");
    expect(pill).toHaveClass("state-pill--validated");
    // Tooltip carries the full version_label for hover.
    expect(badge).toHaveAttribute("title", "Q1 2026 launch");
  });

  it("falls back to a vN tooltip when version_label is null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeTaxonomy({
          version_number: 7,
          version_label: null,
          state: "DRAFT",
        }),
      ),
    );

    renderBadge();

    const badge = await screen.findByTestId("taxonomy-mode-badge");
    expect(badge).toHaveAttribute("title", "Taxonomy v7");
  });

  it("renders nothing on a 503 (no active taxonomy)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "KW_TAXONOMY_UNAVAILABLE",
            message: "No active taxonomy",
            status: 503,
            retryable: true,
            remediation: null,
          },
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    );

    const { container } = renderBadge();

    // Wait a tick so the rejected promise resolves before asserting.
    await waitFor(() => {
      expect(
        screen.queryByTestId("taxonomy-mode-badge"),
      ).not.toBeInTheDocument();
    });
    // The badge is the only thing this component renders; the
    // container should be empty.
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing on a 403 (caller isn't admin)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "KW_FORBIDDEN",
            message: "admin required",
            status: 403,
            retryable: false,
            remediation: null,
          },
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderBadge();

    await waitFor(() => {
      expect(
        screen.queryByTestId("taxonomy-mode-badge"),
      ).not.toBeInTheDocument();
    });
  });

  it("clicking the badge navigates to /admin/taxonomy with the taxonomy_id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeTaxonomy({ taxonomy_id: "tx-launch" })),
    );

    renderBadge();
    const badge = await screen.findByTestId("taxonomy-mode-badge");
    fireEvent.click(badge);

    expect(navigateMock).toHaveBeenCalledWith(
      "/admin/taxonomy?taxonomy_id=tx-launch",
    );
  });
});
