import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders the Orbital title", () => {
    render(<App />);
    expect(
      screen.getByRole("heading", { name: /Orbital — KW Pipeline/i }),
    ).toBeInTheDocument();
  });
});
