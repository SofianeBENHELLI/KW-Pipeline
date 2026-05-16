/**
 * Coverage for the Admin taxonomy versions view (EPIC-1 §1.9).
 *
 * Pinned scenarios:
 * - Empty state — no ``?taxonomy_id=``, no fetch.
 * - Lookup submission fetches the lineage and renders one row per
 *   version with the matching state pill.
 * - Active row is the highest VALIDATED_V1 (falls back to highest
 *   CANDIDATE_V0 / DRAFT when no validated version exists).
 * - Empty lineage (200 + ``versions: []``) renders the "no versions"
 *   hint instead of the table.
 * - 403 collapses the page to "Forbidden" without rendering the form.
 * - 503-style API error renders the inline danger banner.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AdminTaxonomyView,
  StatePill,
  formatTimestamp,
} from "../AdminTaxonomyView";
import type { ApiTaxonomyVersion } from "../../../api/types";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function makeVersion(
  overrides: Partial<ApiTaxonomyVersion> = {},
): ApiTaxonomyVersion {
  return {
    schema_version: "v0.1",
    taxonomy_id: "tx-1",
    version_number: 1,
    version_label: null,
    state: "DRAFT",
    taxonomy: { categories: [] },
    suggestions: [],
    created_at: "2026-05-01T10:00:00Z",
    state_changed_at: "2026-05-01T10:00:00Z",
    created_by: null,
    superseded_version_number: null,
    ...overrides,
  } as ApiTaxonomyVersion;
}

function renderView(initialEntry = "/admin/taxonomy") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AdminTaxonomyView />
    </MemoryRouter>,
  );
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("AdminTaxonomyView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the empty state when no taxonomy_id is set", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderView();
    expect(screen.getByTestId("taxonomy-empty-state")).toBeInTheDocument();
    // No fetch fires on initial render when the input is empty.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("submitting the lookup fetches the lineage and renders one row per version", async () => {
    const versions = [
      makeVersion({
        version_number: 1,
        state: "ARCHIVED",
        version_label: "Initial",
        created_by: "ada",
      }),
      makeVersion({
        version_number: 2,
        state: "VALIDATED_V1",
        version_label: "V1 launch",
        created_by: "bob",
        taxonomy: {
          schema_version: "v0.1",
          categories: [
            {
              id: "battery",
              label: "Battery",
              description: "...",
              source: "imposed",
              subcategories: [
                {
                  id: "battery.thermal",
                  label: "Thermal",
                  description: "...",
                  source: "imposed",
                  subcategories: [],
                },
              ],
            },
          ],
        } as ApiTaxonomyVersion["taxonomy"],
      }),
      makeVersion({
        version_number: 3,
        state: "DRAFT",
        suggestions: [
          {
            label: "Cooling",
            description: "...",
            state: "ACCEPTED",
          } as ApiTaxonomyVersion["suggestions"][number],
        ],
      }),
    ];

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ taxonomy_id: "tx-syn", versions }),
    );

    renderView();
    fireEvent.change(screen.getByTestId("taxonomy-id-input"), {
      target: { value: "tx-syn" },
    });
    fireEvent.click(screen.getByTestId("taxonomy-lookup-submit"));

    const table = await screen.findByTestId("taxonomy-lineage-table");
    expect(table).toBeInTheDocument();

    // One row per version, in the order returned by the API.
    expect(screen.getByTestId("taxonomy-lineage-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("taxonomy-lineage-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("taxonomy-lineage-row-3")).toBeInTheDocument();

    // State pills carry the per-state testid + label.
    expect(screen.getByTestId("state-pill-ARCHIVED")).toHaveTextContent(
      "Archived",
    );
    expect(screen.getByTestId("state-pill-VALIDATED_V1")).toHaveTextContent(
      "Validated",
    );
    expect(screen.getByTestId("state-pill-DRAFT")).toHaveTextContent("Draft");

    // Active row: the highest VALIDATED_V1 (version 2). The DRAFT
    // above does NOT take over — only when there's no validated yet.
    const activeRow = screen.getByTestId("taxonomy-lineage-row-2");
    expect(activeRow).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("taxonomy-lineage-row-3")).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByTestId("taxonomy-lineage-row-1")).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("active row falls back to highest DRAFT when no validated version exists", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        taxonomy_id: "tx-new",
        versions: [
          makeVersion({ version_number: 1, state: "DRAFT" }),
          makeVersion({ version_number: 2, state: "DRAFT" }),
        ],
      }),
    );

    renderView("/admin/taxonomy?taxonomy_id=tx-new");

    const activeRow = await screen.findByTestId("taxonomy-lineage-row-2");
    expect(activeRow).toHaveAttribute("aria-current", "true");
  });

  it("renders the 'no versions' hint on an empty lineage response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ taxonomy_id: "tx-unknown", versions: [] }),
    );

    renderView("/admin/taxonomy?taxonomy_id=tx-unknown");

    expect(
      await screen.findByTestId("taxonomy-no-versions"),
    ).toBeInTheDocument();
    // No table is rendered.
    expect(
      screen.queryByTestId("taxonomy-lineage-table"),
    ).not.toBeInTheDocument();
  });

  it("auto-loads when ?taxonomy_id= is present on first mount", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        taxonomy_id: "tx-q",
        versions: [makeVersion({ version_number: 1 })],
      }),
    );

    renderView("/admin/taxonomy?taxonomy_id=tx-q");

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });
    expect(urlOf(fetchSpy.mock.calls[0]![0])).toContain(
      "/admin/taxonomy/versions/tx-q",
    );
    expect(
      await screen.findByTestId("taxonomy-lineage-row-1"),
    ).toBeInTheDocument();
  });

  it("403 envelope collapses the page to Forbidden", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "KW_FORBIDDEN",
            message: "Admin role required.",
            status: 403,
            retryable: false,
            remediation: null,
          },
          detail: "Admin role required.",
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderView("/admin/taxonomy?taxonomy_id=tx-x");

    expect(await screen.findByText("Forbidden")).toBeInTheDocument();
    expect(
      screen.queryByTestId("taxonomy-lineage-table"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("taxonomy-id-input")).not.toBeInTheDocument();
  });

  it("non-403 API errors render the inline danger banner", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "KW_HTTP_ERROR",
            message: "Backend is misbehaving.",
            status: 500,
            retryable: true,
            remediation: "Retry shortly.",
          },
          detail: "Backend is misbehaving.",
        }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderView("/admin/taxonomy?taxonomy_id=tx-broken");

    expect(
      await screen.findByText("Failed to load taxonomy versions."),
    ).toBeInTheDocument();
    expect(screen.getByText("Retry shortly.")).toBeInTheDocument();
    // The form stays on screen so the operator can retry — only 403
    // collapses to the Forbidden state.
    expect(screen.getByTestId("taxonomy-id-input")).toBeInTheDocument();
  });
});

// ─── Helper coverage ─────────────────────────────────────────────────────────

describe("StatePill", () => {
  it("renders the friendly label for each state", () => {
    const states = [
      ["DRAFT", "Draft"],
      ["CANDIDATE_V0", "Candidate"],
      ["VALIDATED_V1", "Validated"],
      ["ARCHIVED", "Archived"],
      ["DISCARDED", "Discarded"],
    ] as const;
    for (const [state, label] of states) {
      const { unmount } = render(<StatePill state={state} />);
      expect(screen.getByTestId(`state-pill-${state}`)).toHaveTextContent(
        label,
      );
      unmount();
    }
  });
});

describe("formatTimestamp", () => {
  it("formats an ISO Z timestamp as YYYY-MM-DD HH:MM UTC", () => {
    expect(formatTimestamp("2026-05-16T12:34:56Z")).toBe(
      "2026-05-16 12:34 UTC",
    );
  });
  it("returns the raw string for an unparseable input", () => {
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });
});
