/**
 * FsmActions tests — pin button gating, click dispatch, reviewer note
 * forwarding, and the confidence hint.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FsmActions } from "./FsmActions";

const ALL_OFF = {
  extract: false,
  semantic: false,
  validate: false,
  reject: false,
  demote: false,
};

describe("<FsmActions />", () => {
  it("renders all five buttons even when disabled", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
      />,
    );
    expect(screen.getByTestId("kf-fsm-extract")).toBeDisabled();
    expect(screen.getByTestId("kf-fsm-semantic")).toBeDisabled();
    expect(screen.getByTestId("kf-fsm-validate")).toBeDisabled();
    expect(screen.getByTestId("kf-fsm-reject")).toBeDisabled();
    expect(screen.getByTestId("kf-fsm-demote")).toBeDisabled();
  });

  it("the demote button enables when the gate opens (VALIDATED / REJECTED)", () => {
    const onRun = vi.fn();
    render(
      <FsmActions
        gates={{ ...ALL_OFF, demote: true }}
        status="idle"
        activeAction={null}
        error={null}
        onRun={onRun}
      />,
    );
    const button = screen.getByTestId("kf-fsm-demote");
    expect(button).not.toBeDisabled();
    expect(button).toHaveTextContent(/Re-open for review/);
    fireEvent.change(screen.getByLabelText("Reviewer note"), {
      target: { value: "second look" },
    });
    fireEvent.click(button);
    expect(onRun).toHaveBeenCalledWith("demote", "second look");
  });

  it("clicking Validate fires onRun with the typed reviewer note", () => {
    const onRun = vi.fn();
    render(
      <FsmActions
        gates={{ ...ALL_OFF, validate: true }}
        status="idle"
        activeAction={null}
        error={null}
        onRun={onRun}
      />,
    );
    fireEvent.change(screen.getByLabelText("Reviewer note"), {
      target: { value: "looks great" },
    });
    fireEvent.click(screen.getByTestId("kf-fsm-validate"));
    expect(onRun).toHaveBeenCalledWith("validate", "looks great");
  });

  it("buttons reflect the in-flight action via aria-busy + label", () => {
    render(
      <FsmActions
        gates={{ ...ALL_OFF, semantic: true }}
        status="running"
        activeAction="semantic"
        error={null}
        onRun={() => {}}
      />,
    );
    const semantic = screen.getByTestId("kf-fsm-semantic");
    expect(semantic).toHaveAttribute("aria-busy", "true");
    expect(semantic).toHaveTextContent(/Generating…/);
  });

  it("renders the confidence hint when supplied", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
        confidence={0.78}
        autoValidateThreshold={0.85}
      />,
    );
    const hint = screen.getByTestId("kf-fsm-hint");
    expect(hint).toHaveTextContent(/0\.78/);
    expect(hint).toHaveTextContent(/below auto-validate threshold 0\.85/);
  });

  it("renders the error banner on status='error'", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="error"
        activeAction={null}
        error={new Error("boom")}
        onRun={() => {}}
      />,
    );
    const err = screen.getByTestId("kf-fsm-error");
    expect(err).toHaveTextContent(/boom/);
  });
});
