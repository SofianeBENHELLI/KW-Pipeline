/**
 * KnowledgeForgeApp — PR 1 route stub.
 *
 * Verifies the brand wordmark renders, the placeholder body announces
 * the in-progress redesign, and unmatched paths fall back to the same
 * placeholder so deep-linking against a future route doesn't 404.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { KnowledgeForgeApp } from "./KnowledgeForgeApp";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/kf/*" element={<KnowledgeForgeApp />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<KnowledgeForgeApp />", () => {
  it("renders the Knowledge Forge brand wordmark in the top bar", () => {
    renderAt("/kf");
    expect(screen.getByText("Knowledge Forge")).toBeInTheDocument();
  });

  it("renders the placeholder copy on the index route", () => {
    renderAt("/kf");
    expect(
      screen.getByRole("heading", { name: /Knowledge Forge — coming online/i }),
    ).toBeInTheDocument();
  });

  it("falls back to the placeholder on unknown sub-paths", () => {
    renderAt("/kf/this/does/not/exist/yet");
    expect(
      screen.getByRole("heading", { name: /Knowledge Forge — coming online/i }),
    ).toBeInTheDocument();
  });

  it("includes the pipelineName override in the brand crumb when given", () => {
    render(
      <MemoryRouter initialEntries={["/kf"]}>
        <Routes>
          <Route
            path="/kf/*"
            element={<KnowledgeForgeApp pipelineName="kw-pipeline" />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("kw-pipeline · alpha")).toBeInTheDocument();
  });
});
