/**
 * Unit tests for ``withRetry``.
 *
 * Pinned contracts:
 *
 * - 5xx transient responses on idempotent methods retry up to ``maxRetries``.
 * - 4xx responses propagate immediately (no retry).
 * - Non-idempotent methods (POST/PUT/PATCH/DELETE) never retry by default.
 * - Network errors (TypeError) retry on idempotent methods.
 * - ``Retry-After`` (delta-seconds) overrides the computed backoff.
 * - Sleep is fully overridable so tests stay deterministic.
 */

import { describe, expect, it, vi } from "vitest";

import { withRetry } from "./retryFetch";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  });
}

function noopSleep(): Promise<void> {
  return Promise.resolve();
}

describe("withRetry: defaults", () => {
  it("retries 502 on GET twice then succeeds", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("", { status: 502 }))
      .mockResolvedValueOnce(new Response("", { status: 502 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const fetchFn = withRetry(inner, { sleep: noopSleep });
    const res = await fetchFn("https://api.example.com/x");

    expect(res.status).toBe(200);
    expect(inner).toHaveBeenCalledTimes(3);
  });

  it("returns the last 5xx response after exhausting retries", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("", { status: 503 }));

    const fetchFn = withRetry(inner, { sleep: noopSleep, maxRetries: 2 });
    const res = await fetchFn("https://api.example.com/x");

    // After 1 initial + 2 retries = 3 calls, we surface the 503.
    expect(res.status).toBe(503);
    expect(inner).toHaveBeenCalledTimes(3);
  });

  it("does not retry on 4xx", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("", { status: 404 }));

    const fetchFn = withRetry(inner, { sleep: noopSleep });
    const res = await fetchFn("https://api.example.com/x");

    expect(res.status).toBe(404);
    expect(inner).toHaveBeenCalledTimes(1);
  });

  it("does not retry 503 on POST by default", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("", { status: 503 }));

    const fetchFn = withRetry(inner, { sleep: noopSleep });
    const res = await fetchFn("https://api.example.com/x", { method: "POST" });

    // Non-idempotent: returns the 503 without retry to avoid duplicate
    // side-effects on the upstream.
    expect(res.status).toBe(503);
    expect(inner).toHaveBeenCalledTimes(1);
  });

  it("retries 503 on POST when caller opts in", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("", { status: 503 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const fetchFn = withRetry(inner, {
      sleep: noopSleep,
      retryMethods: ["GET", "HEAD", "POST"],
    });
    const res = await fetchFn("https://api.example.com/x", { method: "POST" });

    expect(res.status).toBe(200);
    expect(inner).toHaveBeenCalledTimes(2);
  });
});

describe("withRetry: network errors", () => {
  it("retries TypeError on idempotent methods", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const fetchFn = withRetry(inner, { sleep: noopSleep });
    const res = await fetchFn("https://api.example.com/x");

    expect(res.status).toBe(200);
    expect(inner).toHaveBeenCalledTimes(2);
  });

  it("propagates non-TypeError exceptions immediately", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new Error("AbortError"));

    const fetchFn = withRetry(inner, { sleep: noopSleep });
    await expect(fetchFn("https://api.example.com/x")).rejects.toThrow(
      "AbortError",
    );
    expect(inner).toHaveBeenCalledTimes(1);
  });

  it("does not retry network errors on POST by default", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("network down"));

    const fetchFn = withRetry(inner, { sleep: noopSleep });
    await expect(
      fetchFn("https://api.example.com/x", { method: "POST" }),
    ).rejects.toThrow("network down");
    expect(inner).toHaveBeenCalledTimes(1);
  });

  it("re-raises the last TypeError after exhausting retries", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new TypeError("connection refused"));

    const fetchFn = withRetry(inner, { sleep: noopSleep, maxRetries: 1 });
    await expect(fetchFn("https://api.example.com/x")).rejects.toThrow(
      "connection refused",
    );
    expect(inner).toHaveBeenCalledTimes(2);
  });
});

describe("withRetry: Retry-After", () => {
  it("uses Retry-After (seconds) as the delay between attempts", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response("", {
          status: 503,
          headers: { "Retry-After": "2" },
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const sleeps: number[] = [];
    const sleep = (ms: number) => {
      sleeps.push(ms);
      return Promise.resolve();
    };

    const fetchFn = withRetry(inner, { sleep });
    const res = await fetchFn("https://api.example.com/x");

    expect(res.status).toBe(200);
    // The first sleep should be exactly 2000 ms (2s × 1000), not the
    // computed exponential backoff.
    expect(sleeps[0]).toBe(2000);
  });

  it("falls back to exponential backoff when Retry-After is unparseable", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response("", {
          status: 503,
          headers: { "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT" },
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const sleeps: number[] = [];
    const sleep = (ms: number) => {
      sleeps.push(ms);
      return Promise.resolve();
    };

    const fetchFn = withRetry(inner, { sleep, baseDelayMs: 100 });
    const res = await fetchFn("https://api.example.com/x");

    expect(res.status).toBe(200);
    // Computed exponential (≈100ms × 2^0 = 100ms + jitter up to 100ms),
    // not 0 — proves we didn't accept the HTTP-date as a number.
    expect(sleeps[0]).toBeGreaterThanOrEqual(100);
  });
});

describe("withRetry: validation", () => {
  it("rejects negative maxRetries at construction", () => {
    expect(() => withRetry(globalThis.fetch, { maxRetries: -1 })).toThrow(
      RangeError,
    );
  });

  it("rejects negative baseDelayMs at construction", () => {
    expect(() => withRetry(globalThis.fetch, { baseDelayMs: -1 })).toThrow(
      RangeError,
    );
  });

  it("maxRetries=0 disables retry entirely", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("", { status: 503 }));

    const fetchFn = withRetry(inner, { sleep: noopSleep, maxRetries: 0 });
    const res = await fetchFn("https://api.example.com/x");

    expect(res.status).toBe(503);
    expect(inner).toHaveBeenCalledTimes(1);
  });
});

describe("withRetry: backoff growth", () => {
  it("doubles the delay between retries", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("", { status: 502 }))
      .mockResolvedValueOnce(new Response("", { status: 502 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const sleeps: number[] = [];
    const sleep = (ms: number) => {
      sleeps.push(ms);
      return Promise.resolve();
    };

    const fetchFn = withRetry(inner, { sleep, baseDelayMs: 100 });
    await fetchFn("https://api.example.com/x");

    // Two sleeps: ~100ms (base × 2^0) and ~200ms (base × 2^1), each with
    // up to base of jitter on top.
    expect(sleeps).toHaveLength(2);
    expect(sleeps[0]).toBeGreaterThanOrEqual(100);
    expect(sleeps[0]).toBeLessThan(200 + 1); // exponential + jitter ceiling
    expect(sleeps[1]).toBeGreaterThanOrEqual(200);
    expect(sleeps[1]).toBeLessThan(300 + 1);
  });

  it("caps the delay at backoffCapMs", async () => {
    const inner = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("", { status: 502 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const sleeps: number[] = [];
    const sleep = (ms: number) => {
      sleeps.push(ms);
      return Promise.resolve();
    };

    const fetchFn = withRetry(inner, {
      sleep,
      baseDelayMs: 10_000,
      backoffCapMs: 500,
    });
    await fetchFn("https://api.example.com/x");

    expect(sleeps[0]).toBeLessThanOrEqual(500);
  });
});
