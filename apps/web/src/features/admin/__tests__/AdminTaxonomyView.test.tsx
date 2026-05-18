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
import type {
  ApiConceptSuggestion,
  ApiTaxonomyVersion,
} from "../../../api/types";

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

// ─── Slice 3 — lifecycle actions ──────────────────────────────────────────

function makeConcept(
  overrides: Partial<ApiConceptSuggestion> = {},
): ApiConceptSuggestion {
  return {
    schema_version: "v0.1",
    suggestion_id: "sug-1",
    label: "Battery cooling",
    description: "Proposed subcategory for thermal subsystems.",
    parent_id: null,
    state: "NEW",
    source: "extractor",
    confidence: 0.9,
    evidence_chunk_ids: [],
    merge_target_id: null,
    last_actor: null,
    created_by: null,
    created_at: "2026-05-01T10:00:00Z",
    state_changed_at: "2026-05-01T10:00:00Z",
    ...overrides,
  } as ApiConceptSuggestion;
}

describe("AdminTaxonomyView — lifecycle actions (slice 3)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders state-machine-gated action buttons for each row", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        taxonomy_id: "tx-1",
        versions: [
          makeVersion({ version_number: 1, state: "DRAFT" }),
          makeVersion({ version_number: 2, state: "CANDIDATE_V0" }),
          makeVersion({ version_number: 3, state: "VALIDATED_V1" }),
        ],
      }),
    );
    renderView("/admin/taxonomy?taxonomy_id=tx-1");
    await screen.findByTestId("taxonomy-lineage-row-1");

    // DRAFT row: Promote + Discard enabled; Validate + Archive disabled.
    expect(screen.getByTestId("taxonomy-promote-1")).not.toBeDisabled();
    expect(screen.getByTestId("taxonomy-discard-1")).not.toBeDisabled();
    expect(screen.getByTestId("taxonomy-validate-1")).toBeDisabled();
    expect(screen.getByTestId("taxonomy-archive-1")).toBeDisabled();

    // CANDIDATE_V0 row: Validate + Discard enabled; Promote + Archive disabled.
    expect(screen.getByTestId("taxonomy-validate-2")).not.toBeDisabled();
    expect(screen.getByTestId("taxonomy-discard-2")).not.toBeDisabled();
    expect(screen.getByTestId("taxonomy-promote-2")).toBeDisabled();
    expect(screen.getByTestId("taxonomy-archive-2")).toBeDisabled();

    // VALIDATED_V1 row: only Archive enabled.
    expect(screen.getByTestId("taxonomy-archive-3")).not.toBeDisabled();
    expect(screen.getByTestId("taxonomy-promote-3")).toBeDisabled();
    expect(screen.getByTestId("taxonomy-validate-3")).toBeDisabled();
    expect(screen.getByTestId("taxonomy-discard-3")).toBeDisabled();

    // Synthesize is enabled on DRAFT, disabled on every other state.
    expect(screen.getByTestId("taxonomy-synthesize-1")).not.toBeDisabled();
    expect(screen.getByTestId("taxonomy-synthesize-2")).toBeDisabled();
    expect(screen.getByTestId("taxonomy-synthesize-3")).toBeDisabled();
  });

  it("clicking Promote POSTs to the transition route and refetches", async () => {
    let postUrl = "";
    let postBody: string | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/transition")) {
          postUrl = url;
          postBody =
            input instanceof Request
              ? await input.clone().text()
              : typeof init?.body === "string"
                ? init.body
                : null;
          return makeJsonResponse(makeVersion({ state: "CANDIDATE_V0" }));
        }
        return makeJsonResponse({
          taxonomy_id: "tx-1",
          versions: [makeVersion({ version_number: 1, state: "DRAFT" })],
        });
      },
    );
    renderView("/admin/taxonomy?taxonomy_id=tx-1");
    const btn = await screen.findByTestId("taxonomy-promote-1");
    fireEvent.click(btn);
    await waitFor(() => expect(postUrl).toContain("/transition"));
    expect(postUrl).toContain("/admin/taxonomy/versions/tx-1/1/transition");
    expect(JSON.parse(postBody ?? "{}")).toMatchObject({
      to_state: "CANDIDATE_V0",
    });
  });

  it("clicking Synthesize POSTs to the synthesize route and refetches", async () => {
    let postUrl = "";
    let getCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/synthesize")) {
          postUrl = url;
          return makeJsonResponse(
            makeVersion({
              version_number: 1,
              state: "DRAFT",
              taxonomy: {
                schema_version: "v0.1",
                categories: [
                  {
                    id: "battery",
                    label: "Battery",
                    description: "Synthesized.",
                    source: "imposed",
                    subcategories: [],
                  },
                ],
              } as ApiTaxonomyVersion["taxonomy"],
            }),
          );
        }
        getCount += 1;
        return makeJsonResponse({
          taxonomy_id: "tx-1",
          versions: [makeVersion({ version_number: 1, state: "DRAFT" })],
        });
      },
    );
    renderView("/admin/taxonomy?taxonomy_id=tx-1");
    fireEvent.click(await screen.findByTestId("taxonomy-synthesize-1"));
    await waitFor(() => expect(postUrl).toContain("/synthesize"));
    expect(postUrl).toContain("/admin/taxonomy/versions/tx-1/1/synthesize");
    // Initial load + post-synthesis refetch.
    await waitFor(() => expect(getCount).toBeGreaterThanOrEqual(2));
  });

  it("a 503 KW_LLM_DISABLED on synthesize surfaces the inline error banner", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/synthesize")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                error: {
                  code: "KW_LLM_DISABLED",
                  message: "BusinessTaxonomyCreator is not wired.",
                  status: 503,
                  retryable: false,
                  remediation: "Set KW_LLM_PROVIDER and restart.",
                },
                detail: "BusinessTaxonomyCreator is not wired.",
              }),
              {
                status: 503,
                headers: { "Content-Type": "application/json" },
              },
            ),
          );
        }
        return Promise.resolve(
          makeJsonResponse({
            taxonomy_id: "tx-1",
            versions: [makeVersion({ version_number: 1, state: "DRAFT" })],
          }),
        );
      },
    );
    renderView("/admin/taxonomy?taxonomy_id=tx-1");
    fireEvent.click(await screen.findByTestId("taxonomy-synthesize-1"));
    expect(
      await screen.findByTestId("taxonomy-action-error"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("BusinessTaxonomyCreator is not wired."),
    ).toBeInTheDocument();
  });

  it("a 409 illegal-transition envelope surfaces the inline error banner", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/transition")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                error: {
                  code: "KW_ILLEGAL_TRANSITION",
                  message: "DRAFT → ARCHIVED is not a legal move.",
                  status: 409,
                  retryable: false,
                  remediation: "Promote to CANDIDATE first.",
                },
                detail: "DRAFT → ARCHIVED is not a legal move.",
              }),
              {
                status: 409,
                headers: { "Content-Type": "application/json" },
              },
            ),
          );
        }
        return Promise.resolve(
          makeJsonResponse({
            taxonomy_id: "tx-1",
            versions: [makeVersion({ version_number: 1, state: "DRAFT" })],
          }),
        );
      },
    );
    renderView("/admin/taxonomy?taxonomy_id=tx-1");
    // Drive a Promote — the mocked 409 will surface the banner via
    // the same action-error path.
    fireEvent.click(await screen.findByTestId("taxonomy-promote-1"));
    expect(
      await screen.findByTestId("taxonomy-action-error"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("DRAFT → ARCHIVED is not a legal move."),
    ).toBeInTheDocument();
  });

  it("the Validate modal posts version_label when supplied", async () => {
    let postBody: string | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/transition")) {
          postBody =
            input instanceof Request
              ? await input.clone().text()
              : typeof init?.body === "string"
                ? init.body
                : null;
          return makeJsonResponse(makeVersion({ state: "VALIDATED_V1" }));
        }
        return makeJsonResponse({
          taxonomy_id: "tx-1",
          versions: [makeVersion({ version_number: 2, state: "CANDIDATE_V0" })],
        });
      },
    );
    renderView("/admin/taxonomy?taxonomy_id=tx-1");
    fireEvent.click(await screen.findByTestId("taxonomy-validate-2"));
    fireEvent.change(screen.getByTestId("taxonomy-validate-label"), {
      target: { value: "2026-Q2 launch" },
    });
    fireEvent.click(screen.getByTestId("taxonomy-validate-submit"));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(JSON.parse(postBody!)).toMatchObject({
      to_state: "VALIDATED_V1",
      version_label: "2026-Q2 launch",
    });
  });

  it("Create draft modal posts the body and switches the table to the new lineage", async () => {
    let postUrl = "";
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/admin/taxonomy/drafts")) {
          postUrl = url;
          return makeJsonResponse(
            makeVersion({
              taxonomy_id: "tx-fresh",
              version_number: 1,
              state: "DRAFT",
            }),
          );
        }
        if (url.includes("tx-fresh")) {
          return makeJsonResponse({
            taxonomy_id: "tx-fresh",
            versions: [
              makeVersion({
                taxonomy_id: "tx-fresh",
                version_number: 1,
                state: "DRAFT",
              }),
            ],
          });
        }
        return makeJsonResponse({ taxonomy_id: "tx-1", versions: [] });
      },
    );
    renderView();
    fireEvent.click(screen.getByTestId("taxonomy-create-draft"));
    fireEvent.click(screen.getByTestId("taxonomy-draft-submit"));
    await waitFor(() => expect(postUrl).toContain("/admin/taxonomy/drafts"));
    // After creation the table jumps to the new lineage.
    expect(
      await screen.findByTestId("taxonomy-lineage-row-1"),
    ).toBeInTheDocument();
  });

  it("expands a version's concepts panel and accepts a suggestion", async () => {
    let postUrl = "";
    let postBody: string | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/concepts/")) {
          postUrl = url;
          postBody =
            input instanceof Request
              ? await input.clone().text()
              : typeof init?.body === "string"
                ? init.body
                : null;
          return makeJsonResponse(makeConcept({ state: "ACCEPTED" }));
        }
        return makeJsonResponse({
          taxonomy_id: "tx-1",
          versions: [
            makeVersion({
              version_number: 1,
              state: "DRAFT",
              suggestions: [makeConcept({ suggestion_id: "sug-1" })],
            }),
          ],
        });
      },
    );
    renderView("/admin/taxonomy?taxonomy_id=tx-1");
    fireEvent.click(await screen.findByTestId("taxonomy-concepts-toggle-1"));
    expect(screen.getByTestId("taxonomy-concepts-panel-1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("taxonomy-concept-accept-sug-1"));
    await waitFor(() => expect(postUrl).toContain("/concepts/"));
    expect(postUrl).toContain(
      "/admin/taxonomy/versions/tx-1/1/concepts/sug-1/transition",
    );
    expect(JSON.parse(postBody!)).toMatchObject({ to_state: "ACCEPTED" });
  });
});
