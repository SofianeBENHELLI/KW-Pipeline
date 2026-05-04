import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettingsPanel } from "./SettingsPanel";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const BASE_PROPS = {
  initialValue: "http://existing",
  onSave: vi.fn(),
  onCancel: vi.fn(),
};

describe("SettingsPanel (widget)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    BASE_PROPS.onSave.mockReset();
    BASE_PROPS.onCancel.mockReset();
  });

  it("renders the input pre-populated with initialValue", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<SettingsPanel {...BASE_PROPS} />);

    const input = screen.getByLabelText("API base URL") as HTMLInputElement;
    expect(input.value).toBe("http://existing");
    expect(screen.getByText(/Checking reachability/)).toBeInTheDocument();
  });

  it("renders the reachable status line on a successful probe", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ status: "ok", version: "1.0.0" }),
    );

    render(<SettingsPanel {...BASE_PROPS} />);

    expect(await screen.findByText(/Currently reachable/)).toBeInTheDocument();
    expect(screen.getByText(/1\.0\.0/)).toBeInTheDocument();
  });

  it("renders the unreachable status line when the probe rejects", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("DNS"));

    render(<SettingsPanel {...BASE_PROPS} />);

    expect(await screen.findByText(/Unreachable · DNS/)).toBeInTheDocument();
  });

  it("disables Save when the input is empty/whitespace", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<SettingsPanel {...BASE_PROPS} />);

    fireEvent.change(screen.getByLabelText("API base URL"), {
      target: { value: "   " },
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("calls onSave with the trimmed value when Save is clicked", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    const onSave = vi.fn();
    render(<SettingsPanel {...BASE_PROPS} onSave={onSave} />);

    fireEvent.change(screen.getByLabelText("API base URL"), {
      target: { value: "  http://new-host  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith("http://new-host");
  });

  it("calls onCancel when Cancel is clicked", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    const onCancel = vi.fn();
    render(<SettingsPanel {...BASE_PROPS} onCancel={onCancel} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
