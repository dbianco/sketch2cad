import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { createEditorStore } from "../store/entities";
import { HISTORY_TEXT, HistoryButtons } from "./HistoryButtons";

function setup(disabled = false) {
  const store = createEditorStore();
  store.getState().load("rev", []);
  render(<HistoryButtons store={store} disabled={disabled} />);
  const add = () => {
    let id = "";
    act(() => {
      id = store.getState().addEntity({
        type: "point",
        layer: "L_STRUCTURE",
        geometry: { kind: "point", coords_m: [[1, 2]] },
        attributes: {},
      });
    });
    return id;
  };
  const live = () => {
    const { byId, order } = store.getState().entities;
    return order.filter((id) => byId[id]?.deleted === false);
  };
  return { store, add, live };
}

const undoButton = () => screen.getByRole<HTMLButtonElement>("button", { name: HISTORY_TEXT.undo });
const redoButton = () => screen.getByRole<HTMLButtonElement>("button", { name: HISTORY_TEXT.redo });

afterEach(cleanup);

describe("HistoryButtons", () => {
  it("disables both buttons while there is nothing to undo or redo", () => {
    setup();
    expect(undoButton()).toHaveProperty("disabled", true);
    expect(redoButton()).toHaveProperty("disabled", true);
  });

  it("undoes and redoes with the buttons", () => {
    const { add, live } = setup();
    const id = add();
    fireEvent.click(undoButton());
    expect(live()).toEqual([]);
    expect(redoButton()).toHaveProperty("disabled", false);
    fireEvent.click(redoButton());
    expect(live()).toEqual([id]);
  });

  it("undoes with Ctrl+Z or Cmd+Z and redoes with Shift", () => {
    const { add, live } = setup();
    const id = add();
    fireEvent.keyDown(document, { key: "z", ctrlKey: true });
    expect(live()).toEqual([]);
    fireEvent.keyDown(document, { key: "Z", metaKey: true, shiftKey: true });
    expect(live()).toEqual([id]);
    fireEvent.keyDown(document, { key: "z", metaKey: true });
    expect(live()).toEqual([]);
  });

  it("ignores Z without a modifier, with Alt, and while typing in a field", () => {
    const { add, live } = setup();
    const id = add();
    fireEvent.keyDown(document, { key: "z" });
    fireEvent.keyDown(document, { key: "z", ctrlKey: true, altKey: true });
    const input = document.createElement("input");
    document.body.append(input);
    fireEvent.keyDown(input, { key: "z", ctrlKey: true });
    input.remove();
    expect(live()).toEqual([id]);
  });

  it("does nothing on a read-only plan", () => {
    const { add, live } = setup(true);
    const id = add();
    expect(undoButton()).toHaveProperty("disabled", true);
    fireEvent.keyDown(document, { key: "z", ctrlKey: true });
    expect(live()).toEqual([id]);
  });
});
