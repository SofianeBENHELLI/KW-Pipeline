import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders the compact pipeline widget and review workspace", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: /KW Pipeline/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Recent documents/i })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /supplier-quality-policy.txt/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Raw extraction/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Semantic output/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Markdown preview/i })).toBeInTheDocument();
  });

  it("surfaces review and failure states in the widget", () => {
    render(<App />);

    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Validated").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Upload document/i })).toBeInTheDocument();
  });
});
