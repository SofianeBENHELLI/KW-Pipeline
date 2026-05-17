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

// Helper for one ``ConceptSuggestion`` row. The route shape only cares
// about the id + state + label fields the action tests assert on.
function makeSuggestion(
  overrides: Partial<ApiTaxonomyVersion["suggestions"][number]> = {},
): ApiTaxonomyVersion["suggestions"][number] {
  return {
    suggestion_id: "sug-1",
    label: "Cooling",
    description: "Battery cooling subsystem.",
    parent_id: null,
    state: "NEW",
    source: "extractor",
    confidence: 0.9,
    evidence_chunk_ids: [],
    created_at: "2026-05-01T10:00:00Z",
    state_changed_at: "2026-05-01T10:00:00Z",
    created_by: null,
    last_actor: null,
    merge_target_id: null,
    schema_version: "v0.1",
    ...overrides,
  } as ApiTaxonomyVersion["suggestions"][number];
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

// ─── Action button coverage (slice 1.9 follow-up) ───────────────────────────

/** Wire one fetch mock that returns ``listResponses[i]`` for the i-th
 *  GET /admin/taxonomy/versions/{id} call and routes every POST to the
 *  ``onPost`` handler. Lets the action tests assert "POST fires + list
 *  re-fetches" with a single mock. */
function installListPostMock(
  listResponses: ApiTaxonomyVersion[][],
  onPost: (url: string, body: unknown) => Response | Promise<Response>,
) {
  let listIdx = 0;
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "GET" && url.includes("/admin/taxonomy/versions/")) {
          const next =
            listResponses[Math.min(listIdx, listResponses.length - 1)];
          listIdx += 1;
          return makeJsonResponse({
            taxonomy_id: "tx-act",
            versions: next ?? [],
          });
        }
        if (method === "POST") {
          let body: unknown = null;
          if (input instanceof Request) {
            try {
              body = await input.clone().json();
            } catch {
              body = null;
            }
          } else if (init?.body !== undefined) {
            try {
              body = JSON.parse(init.body as string);
            } catch {
              body = null;
            }
          }
          return onPost(url, body);
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );
}

describe("AdminTaxonomyView — version actions", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Promote button POSTs to_state=CANDIDATE_V0 and refetches the lineage", async () => {
    let promoteCalled = false;
    let promoteBody: unknown = null;
    let promoteUrl = "";
    const draft = makeVersion({
      version_number: 1,
      state: "DRAFT",
      taxonomy_id: "tx-act",
    });
    const candidate = { ...draft, state: "CANDIDATE_V0" as const };
    installListPostMock([[draft], [candidate]], (url, body) => {
      if (url.includes("/transition")) {
        promoteCalled = true;
        promoteBody = body;
        promoteUrl = url;
        return makeJsonResponse(candidate);
      }
      return makeJsonResponse({ detail: "unexpected" }, 500);
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    fireEvent.click(await screen.findByTestId("action-promote"));

    await waitFor(() => {
      expect(promoteCalled).toBe(true);
    });
    expect(promoteUrl).toContain(
      "/admin/taxonomy/versions/tx-act/1/transition",
    );
    expect(promoteBody).toEqual({ to_state: "CANDIDATE_V0" });
    // Lineage refetched and the row now shows the Candidate pill.
    await waitFor(() => {
      expect(screen.getByTestId("state-pill-CANDIDATE_V0")).toBeInTheDocument();
    });
  });

  it("Validate modal submits to_state=VALIDATED_V1 with the typed label", async () => {
    let validateBody: unknown = null;
    const candidate = makeVersion({
      version_number: 2,
      state: "CANDIDATE_V0",
      taxonomy_id: "tx-act",
    });
    const validated = {
      ...candidate,
      state: "VALIDATED_V1" as const,
      version_label: "Launch",
    };
    installListPostMock([[candidate], [validated]], (_url, body) => {
      validateBody = body;
      return makeJsonResponse(validated);
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    fireEvent.click(await screen.findByTestId("action-validate"));
    fireEvent.change(await screen.findByTestId("validate-version-label"), {
      target: { value: "Launch" },
    });
    fireEvent.click(screen.getByTestId("validate-submit"));

    await waitFor(() => {
      expect(validateBody).toEqual({
        to_state: "VALIDATED_V1",
        version_label: "Launch",
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("state-pill-VALIDATED_V1")).toBeInTheDocument();
    });
  });

  it("Archive modal submits to_state=ARCHIVED with the optional reason", async () => {
    let archiveBody: unknown = null;
    const validated = makeVersion({
      version_number: 3,
      state: "VALIDATED_V1",
      taxonomy_id: "tx-act",
    });
    const archived = { ...validated, state: "ARCHIVED" as const };
    installListPostMock([[validated], [archived]], (_url, body) => {
      archiveBody = body;
      return makeJsonResponse(archived);
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    fireEvent.click(await screen.findByTestId("action-archive"));
    fireEvent.change(await screen.findByTestId("reason-input"), {
      target: { value: "superseded by v4" },
    });
    fireEvent.click(screen.getByTestId("reason-submit"));

    await waitFor(() => {
      expect(archiveBody).toEqual({
        to_state: "ARCHIVED",
        reason: "superseded by v4",
      });
    });
  });

  it("Discard modal with an empty reason sends reason=null", async () => {
    let discardBody: unknown = null;
    const draft = makeVersion({
      version_number: 1,
      state: "DRAFT",
      taxonomy_id: "tx-act",
    });
    const discarded = { ...draft, state: "DISCARDED" as const };
    installListPostMock([[draft], [discarded]], (_url, body) => {
      discardBody = body;
      return makeJsonResponse(discarded);
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    fireEvent.click(await screen.findByTestId("action-discard"));
    // No reason typed — submit with the empty default.
    fireEvent.click(await screen.findByTestId("reason-submit"));

    await waitFor(() => {
      expect(discardBody).toEqual({ to_state: "DISCARDED", reason: null });
    });
  });

  it("Synthesize button POSTs to .../synthesize and refetches the lineage", async () => {
    let synthCalled = false;
    let synthUrl = "";
    const draft = makeVersion({
      version_number: 1,
      state: "DRAFT",
      taxonomy_id: "tx-act",
    });
    const draftWithTree = {
      ...draft,
      taxonomy: {
        schema_version: "v0.1",
        categories: [
          {
            id: "battery",
            label: "Battery",
            description: "...",
            source: "imposed",
            subcategories: [],
          },
        ],
      } as ApiTaxonomyVersion["taxonomy"],
    };
    installListPostMock([[draft], [draftWithTree]], (url, _body) => {
      if (url.includes("/synthesize")) {
        synthCalled = true;
        synthUrl = url;
        return makeJsonResponse(draftWithTree);
      }
      return makeJsonResponse({ detail: "unexpected" }, 500);
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    fireEvent.click(await screen.findByTestId("action-synthesize"));

    await waitFor(() => {
      expect(synthCalled).toBe(true);
    });
    expect(synthUrl).toContain("/admin/taxonomy/versions/tx-act/1/synthesize");
    // Lineage refetched — category count cell now reads 1.
    await waitFor(() => {
      const row = screen.getByTestId("taxonomy-lineage-row-1");
      expect(row).toHaveTextContent("1");
    });
  });

  it("surfaces a 409 illegal-transition envelope in the inline error banner", async () => {
    const candidate = makeVersion({
      version_number: 1,
      state: "CANDIDATE_V0",
      taxonomy_id: "tx-act",
    });
    installListPostMock([[candidate]], () => {
      return new Response(
        JSON.stringify({
          error: {
            code: "KW_ILLEGAL_TAXONOMY_TRANSITION",
            message:
              "Illegal transition CANDIDATE_V0 -> CANDIDATE_V0 (ADR-018 §2).",
            status: 409,
            retryable: false,
            remediation: null,
          },
          detail:
            "Illegal transition CANDIDATE_V0 -> CANDIDATE_V0 (ADR-018 §2).",
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      );
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    // The validate-modal path drives the failing POST.
    fireEvent.click(await screen.findByTestId("action-validate"));
    fireEvent.click(await screen.findByTestId("validate-submit"));

    expect(
      await screen.findByTestId("taxonomy-action-error"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Illegal transition/)).toBeInTheDocument();
    // The row is still there — a failed mutation does not wipe state.
    expect(screen.getByTestId("taxonomy-lineage-row-1")).toBeInTheDocument();
  });
});

describe("AdminTaxonomyView — create draft", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Create draft button opens the modal and POSTs the empty body for a fresh lineage", async () => {
    let postCalled = false;
    let postUrl = "";
    let postBody: unknown = null;
    const fresh = makeVersion({
      version_number: 1,
      state: "DRAFT",
      taxonomy_id: "tx-fresh",
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        const method =
          input instanceof Request ? input.method : (init?.method ?? "GET");
        if (method === "POST" && url.includes("/admin/taxonomy/drafts")) {
          postCalled = true;
          postUrl = url;
          if (input instanceof Request) {
            try {
              postBody = await input.clone().json();
            } catch {
              postBody = null;
            }
          }
          return makeJsonResponse(fresh);
        }
        if (
          method === "GET" &&
          url.includes("/admin/taxonomy/versions/tx-fresh")
        ) {
          return makeJsonResponse({
            taxonomy_id: "tx-fresh",
            versions: [fresh],
          });
        }
        return makeJsonResponse({ detail: "unexpected" }, 500);
      },
    );

    renderView();

    fireEvent.click(screen.getByTestId("create-draft-button"));
    // Modal fields are empty by default — submit "as-is".
    fireEvent.click(await screen.findByTestId("create-draft-submit"));

    await waitFor(() => {
      expect(postCalled).toBe(true);
    });
    expect(postUrl).toContain("/admin/taxonomy/drafts");
    expect(postBody).toEqual({});
    // The view switches the applied id over to the new lineage.
    await waitFor(() => {
      expect(screen.getByTestId("taxonomy-lineage-row-1")).toBeInTheDocument();
    });
  });
});

describe("AdminTaxonomyView — concept transitions", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Accept on a NEW suggestion POSTs to_state=ACCEPTED and refetches", async () => {
    let acceptCalled = false;
    let acceptUrl = "";
    let acceptBody: unknown = null;
    const suggestion = makeSuggestion({
      suggestion_id: "sug-cooling",
      state: "NEW",
    });
    const draft = makeVersion({
      version_number: 1,
      state: "DRAFT",
      taxonomy_id: "tx-act",
      suggestions: [suggestion],
    });
    const accepted = {
      ...draft,
      suggestions: [{ ...suggestion, state: "ACCEPTED" as const }],
    };
    installListPostMock([[draft], [accepted]], (url, body) => {
      if (url.includes("/concepts/")) {
        acceptCalled = true;
        acceptUrl = url;
        acceptBody = body;
        return makeJsonResponse({ ...suggestion, state: "ACCEPTED" });
      }
      return makeJsonResponse({ detail: "unexpected" }, 500);
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    // Expand the concepts sub-table.
    fireEvent.click(await screen.findByTestId("action-toggle-concepts"));
    fireEvent.click(await screen.findByTestId("concept-accept-sug-cooling"));

    await waitFor(() => {
      expect(acceptCalled).toBe(true);
    });
    expect(acceptUrl).toContain(
      "/admin/taxonomy/versions/tx-act/1/concepts/sug-cooling/transition",
    );
    expect(acceptBody).toEqual({ to_state: "ACCEPTED" });
  });

  it("Merge with an empty target id surfaces the 400 in the banner", async () => {
    const suggestion = makeSuggestion({
      suggestion_id: "sug-cooling",
      state: "UNDER_REVIEW",
    });
    const draft = makeVersion({
      version_number: 1,
      state: "DRAFT",
      taxonomy_id: "tx-act",
      suggestions: [suggestion],
    });
    installListPostMock([[draft]], (url) => {
      if (url.includes("/concepts/")) {
        return new Response(
          JSON.stringify({
            error: {
              code: "KW_BAD_REQUEST",
              message:
                "merge_target_id is required when transitioning to MERGED.",
              status: 400,
              retryable: false,
              remediation: null,
            },
            detail: "merge_target_id is required when transitioning to MERGED.",
          }),
          { status: 400, headers: { "Content-Type": "application/json" } },
        );
      }
      return makeJsonResponse({ detail: "unexpected" }, 500);
    });

    renderView("/admin/taxonomy?taxonomy_id=tx-act");

    fireEvent.click(await screen.findByTestId("action-toggle-concepts"));
    fireEvent.click(await screen.findByTestId("concept-merge-sug-cooling"));
    // Submit without typing a target id — the server returns the 400.
    fireEvent.click(await screen.findByTestId("merge-submit"));

    expect(
      await screen.findByText(/merge_target_id is required/),
    ).toBeInTheDocument();
  });
});
