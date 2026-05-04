import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HealthCard } from "./HealthCard";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const BASE_PROPS = {
  apiBaseUrl: "http://test",
  refreshTick: 0,
};

describe("HealthCard (widget)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the API base URL chip immediately", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<HealthCard {...BASE_PROPS} />);
    expect(screen.getByText("API")).toBeInTheDocument();
    expect(screen.getByText("http://test")).toBeInTheDocument();
    expect(screen.getByText(/Checking/)).toBeInTheDocument();
  });

  it("renders status + version + latency on a successful health response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ status: "ok", version: "1.2.3" }),
    );

    render(<HealthCard {...BASE_PROPS} />);

    expect(await screen.findByText("ok")).toBeInTheDocument();
    expect(screen.getByText("1.2.3")).toBeInTheDocument();
    // Latency rendered as "<n> ms"; we only assert the suffix because
    // performance.now() values are non-deterministic.
    expect(screen.getByText(/ms$/)).toBeInTheDocument();
  });

  it("falls back to em-dash when the backend omits version", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ status: "ok" }),
    );

    render(<HealthCard {...BASE_PROPS} />);

    expect(await screen.findByText("ok")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders 'unreachable' + the error code on ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_INTERNAL",
            message: "Boom",
            status: 500,
            retryable: true,
          },
          detail: "Boom",
        },
        500,
      ),
    );

    render(<HealthCard {...BASE_PROPS} />);

    expect(await screen.findByText(/unreachable/)).toBeInTheDocument();
    expect(screen.getByText(/KW_INTERNAL/)).toBeInTheDocument();
  });

  it("renders the network error message when fetch itself rejects", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));

    render(<HealthCard {...BASE_PROPS} />);

    expect(await screen.findByText(/unreachable/)).toBeInTheDocument();
    expect(screen.getByText(/network down/)).toBeInTheDocument();
  });
});
