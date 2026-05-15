/**
 * FsmActions tests — pin button gating, click dispatch, reviewer note
 * forwarding, and the confidence hint.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FsmActions } from "./FsmActions";
import { SEMANTIC_METHOD_OPTIONS } from "./semanticMethods";

const ALL_OFF = {
  extract: false,
  semantic: false,
  "semantic-rerun": false,
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

  it("exposes the semantic-method dropdown with the registered options", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
      />,
    );
    const select = screen.getByTestId(
      "kf-fsm-semantic-method",
    ) as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    // Three options today: structure_first (M1), semantic_intelligence
    // (M2), knowledge_graph (M3). Method 1 is the runtime default.
    expect(select.options.length).toBe(SEMANTIC_METHOD_OPTIONS.length);
    expect([...select.options].map((o) => o.value)).toEqual(
      SEMANTIC_METHOD_OPTIONS.map((o) => o.id),
    );
  });

  it("changing the dropdown fires onSemanticMethodChange with the picked id", () => {
    const onSemanticMethodChange = vi.fn();
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
        semanticMethod="structure_first"
        onSemanticMethodChange={onSemanticMethodChange}
      />,
    );
    // Pick Method 2 — Method 3 is currently disabled in the dropdown
    // ("under development") so we exercise the change handler against
    // an enabled target.
    fireEvent.change(screen.getByTestId("kf-fsm-semantic-method"), {
      target: { value: "semantic_intelligence" },
    });
    expect(onSemanticMethodChange).toHaveBeenCalledWith("semantic_intelligence");
  });

  it("Method 3 — knowledge_graph is rendered but disabled (under development)", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
      />,
    );
    const option = screen.getByTestId(
      "kf-fsm-semantic-method-option-knowledge_graph",
    ) as HTMLOptionElement;
    expect(option).toBeDisabled();
    expect(option.textContent).toMatch(/under development/i);
  });

  it("the other two methods are enabled in the dropdown", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
      />,
    );
    expect(
      screen.getByTestId("kf-fsm-semantic-method-option-structure_first"),
    ).not.toBeDisabled();
    expect(
      screen.getByTestId(
        "kf-fsm-semantic-method-option-semantic_intelligence",
      ),
    ).not.toBeDisabled();
  });

  it("disables the dropdown while a transition is in flight", () => {
    render(
      <FsmActions
        gates={{ ...ALL_OFF, semantic: true }}
        status="running"
        activeAction="semantic"
        error={null}
        onRun={() => {}}
      />,
    );
    expect(screen.getByTestId("kf-fsm-semantic-method")).toBeDisabled();
  });

  it("dropdown lists all three semantic methods (Methods 1 / 2 / 3)", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
      />,
    );
    const select = screen.getByTestId(
      "kf-fsm-semantic-method",
    ) as HTMLSelectElement;
    expect(select.options.length).toBe(3);
    expect([...select.options].map((o) => o.value)).toEqual([
      "structure_first",
      "semantic_intelligence",
      "knowledge_graph",
    ]);
  });

  it("default-selected method is Method 1 — structure_first", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
      />,
    );
    const select = screen.getByTestId(
      "kf-fsm-semantic-method",
    ) as HTMLSelectElement;
    expect(select.value).toBe("structure_first");
  });

  it("Re-run button is disabled until semantic output exists", () => {
    render(
      <FsmActions
        gates={ALL_OFF}
        status="idle"
        activeAction={null}
        error={null}
        onRun={() => {}}
      />,
    );
    expect(screen.getByTestId("kf-fsm-semantic-rerun")).toBeDisabled();
  });

  it("Re-run button fires onRun('semantic-rerun') when enabled", () => {
    const onRun = vi.fn();
    render(
      <FsmActions
        gates={{ ...ALL_OFF, "semantic-rerun": true }}
        status="idle"
        activeAction={null}
        error={null}
        onRun={onRun}
      />,
    );
    fireEvent.click(screen.getByTestId("kf-fsm-semantic-rerun"));
    expect(onRun).toHaveBeenCalledWith("semantic-rerun", undefined);
  });

  it("Re-run button shows 'Re-running…' + aria-busy while in flight", () => {
    render(
      <FsmActions
        gates={{ ...ALL_OFF, "semantic-rerun": true }}
        status="running"
        activeAction="semantic-rerun"
        error={null}
        onRun={() => {}}
      />,
    );
    const rerun = screen.getByTestId("kf-fsm-semantic-rerun");
    expect(rerun).toHaveAttribute("aria-busy", "true");
    expect(rerun).toHaveTextContent(/Re-running…/);
  });
});
