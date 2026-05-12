/**
 * `buildOpenInOrbitalUrl` — pin the URL builder used by the "open in
 * Orbital" button on each document row. The previous one-liner
 * appended `/?document=…` even when the configured URL ended in
 * `index.html`, producing `…/index.html/?document=…` — which to S3
 * looks like a request for a key named `index.html/` and returns 403
 * AccessDenied. This test pins the behaviour for the three URL
 * shapes operators typically configure.
 */

import { describe, expect, it } from "vitest";

import { buildOpenInOrbitalUrl } from "./App";

describe("buildOpenInOrbitalUrl", () => {
  it("appends `?document=…` to a bare-host URL", () => {
    expect(buildOpenInOrbitalUrl("https://example.com/orbital", "doc-1")).toBe(
      "https://example.com/orbital?document=doc-1",
    );
  });

  it("preserves a trailing slash when the URL is the SPA root", () => {
    expect(buildOpenInOrbitalUrl("https://example.com/orbital/", "doc-1")).toBe(
      "https://example.com/orbital/?document=doc-1",
    );
  });

  it("does NOT inject a slash when the URL points at index.html (the regression)", () => {
    const url = buildOpenInOrbitalUrl(
      "https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledge-orbital/v0.1.0/index.html",
      "fa98c69e-ac95-4ab1-b7f3-acefc65f9c68",
    );
    // Crucially: there's no `index.html/?` — the trailing slash that
    // tripped S3 would have made this return 403 AccessDenied.
    expect(url).toBe(
      "https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledge-orbital/v0.1.0/index.html?document=fa98c69e-ac95-4ab1-b7f3-acefc65f9c68",
    );
    expect(url).not.toContain("index.html/?");
  });

  it("merges with an existing query string", () => {
    expect(
      buildOpenInOrbitalUrl(
        "https://example.com/orbital/index.html?theme=dark",
        "doc-2",
      ),
    ).toBe(
      "https://example.com/orbital/index.html?theme=dark&document=doc-2",
    );
  });

  it("URL-encodes document ids with reserved characters", () => {
    expect(
      buildOpenInOrbitalUrl(
        "https://example.com/orbital/index.html",
        "weird id with space & ?",
      ),
    ).toBe(
      "https://example.com/orbital/index.html?document=weird+id+with+space+%26+%3F",
    );
  });

  it("falls back to a string concat when the URL is unparseable", () => {
    expect(buildOpenInOrbitalUrl("not a url", "doc-3")).toBe(
      "not a url?document=doc-3",
    );
  });
});
