import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EditorWorkspace } from "../EditorWorkspace";
import type { Catalog } from "../canvas/EditorCanvas";
import type { Viewport } from "../canvas/viewport";
import { TOOL_BUTTONS } from "../panels/Palette";
import { createSnapSettingsStore } from "../panels/SnapSettings";
import { createEditorStore } from "../store/entities";
import type { EditorEntity, Point2 } from "../store/types";
import { TEXT_TOOL_TEXT } from "../tools/PointTextTools";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";
import { CURSOR_TEXT, followCursor, movedCursor } from "./keyboardCursor";
import { KEYBOARD_HELP_TEXT } from "./KeyboardHelp";
import {
  COARSE_STEP_FACTOR,
  FINE_STEP_FACTOR,
  SHORTCUT_GROUPS,
  TOOL_SHORTCUTS,
  cursorDelta,
  stepMultiplier,
} from "./shortcuts";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const GRID_M = 0.5;

const CATALOG: Catalog = {
  id: "00000000-0000-4000-8000-0000000000c1",
  version: "default-1",
  layers: [
    { name: "L_STRUCTURE", description: "Muros", aci_color: 7 },
    { name: "L_SHEET", description: "Cajetín", aci_color: 7 },
  ],
  symbols: [],
};

function setup(tool: ToolId = "select") {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, []);
  const ui = createEditorUiStore({ activeTool: tool });
  const snap = createSnapSettingsStore(null);
  snap.getState().setGridM(GRID_M);
  let viewport: Viewport | null = null;
  render(
    <EditorWorkspace
      catalog={CATALOG}
      store={store}
      ui={ui}
      snap={snap}
      width={800}
      height={600}
      onViewportChange={(next) => {
        viewport = next;
      }}
    />,
  );
  const svg = screen.getByTestId("editor-canvas") as unknown as SVGSVGElement;
  svg.getBoundingClientRect = () => new DOMRect(0, 0, 800, 600);
  const camera = (): Viewport => {
    if (viewport === null) throw new Error("No viewport yet");
    return viewport;
  };
  return { store, ui, svg, camera };
}

function key(target: Element, name: string, init: Record<string, unknown> = {}) {
  fireEvent.keyDown(target, { key: name, ...init });
}
function created(store: ReturnType<typeof createEditorStore>): EditorEntity[] {
  const { byId, order } = store.getState().entities;
  return order.filter((id) => id.startsWith("local-")).map((id) => byId[id] as EditorEntity);
}
function close(actual: Point2 | undefined, expected: Point2) {
  expect(actual?.[0]).toBeCloseTo(expected[0], 9);
  expect(actual?.[1]).toBeCloseTo(expected[1], 9);
}

afterEach(cleanup);

describe("shortcuts", () => {
  it("is the single source of the palette's tool keys", () => {
    for (const button of TOOL_BUTTONS) expect(button.shortcut).toBe(TOOL_SHORTCUTS[button.id]);
    const tools = SHORTCUT_GROUPS[0]?.entries.map((entry) => entry.keys);
    expect(tools).toEqual(["V", "E", "L", "P", "O", "T", "S", "F", "D", "A", "G", "B"]);
  });

  it("maps arrows and modifiers to cursor steps", () => {
    const none = { shiftKey: false, altKey: false, ctrlKey: false };
    expect(stepMultiplier(none)).toBe(1);
    expect(stepMultiplier({ ...none, shiftKey: true })).toBe(FINE_STEP_FACTOR);
    expect(stepMultiplier({ ...none, altKey: true })).toBe(COARSE_STEP_FACTOR);
    expect(stepMultiplier({ ...none, ctrlKey: true, shiftKey: true })).toBe(COARSE_STEP_FACTOR);
    expect(cursorDelta({ ...none, key: "ArrowUp" }, 2)).toEqual([0, -2]);
    expect(cursorDelta({ ...none, key: "ArrowLeft", shiftKey: true }, 2)).toEqual([-0.2, 0]);
    expect(cursorDelta({ ...none, key: "a" }, 2)).toBeNull();
    expect(movedCursor([1, 1], [0, -2])).toEqual([1, 3]);
  });

  it("pans just enough to keep the cursor inside the canvas", () => {
    const viewport: Viewport = { centerX: 0, centerY: 0, scale: 10, width: 200, height: 100 };
    expect(followCursor(viewport, [0, 0])).toBe(viewport);
    const moved = followCursor(viewport, [20, 0]);
    expect(moved.centerX).toBeGreaterThan(0);
    expect(moved.centerY).toBe(0);
  });
});

describe("keyboard cursor", () => {
  it("creates a line with the tool key, arrows and Enter only", () => {
    const { store, ui, svg, camera } = setup();
    svg.focus();
    key(svg, "l");
    expect(ui.getState().activeTool).toBe("line");
    const centre: Point2 = [camera().centerX, camera().centerY];

    key(svg, "Enter");
    for (let i = 0; i < 4; i += 1) key(svg, "ArrowRight");
    key(svg, "ArrowRight", { shiftKey: true });
    key(svg, "Enter");

    const [line] = created(store);
    expect(created(store)).toHaveLength(1);
    expect(line?.geometry.kind).toBe("line");
    const coords = line?.geometry.kind === "line" ? line.geometry.coords_m : [];
    close(coords[0], centre);
    close(coords[1], [centre[0] + (4 + FINE_STEP_FACTOR) * GRID_M, centre[1]]);
    expect(ui.getState().draft).toEqual([]);
  });

  it("shows a crosshair and announces the local coordinates; the mouse hides it", () => {
    const { svg, camera } = setup("point");
    svg.focus();
    expect(screen.queryByTestId("keyboard-cursor")).toBeNull();
    const centre: Point2 = [camera().centerX, camera().centerY];
    key(svg, "ArrowDown", { altKey: true });
    expect(screen.getByTestId("keyboard-cursor")).toBeTruthy();
    expect(screen.getByText(CURSOR_TEXT.at([centre[0], centre[1] - 10 * GRID_M]))).toBeTruthy();
    expect(svg.getAttribute("aria-describedby")).toBe("editor-canvas-keys");
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 10, clientY: 10 });
    expect(screen.queryByTestId("keyboard-cursor")).toBeNull();
  });

  it("places a point with Space at the cursor, starting from the last pointer position", () => {
    const { store, svg, camera } = setup("point");
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 400, clientY: 300 });
    const centre: Point2 = [camera().centerX, camera().centerY];
    svg.focus();
    key(svg, "ArrowLeft");
    key(svg, " ");
    const [point] = created(store);
    expect(point?.geometry).toMatchObject({ kind: "point" });
    const coords = point?.geometry.kind === "point" ? point.geometry.coords_m : [];
    close(coords[0], [centre[0] - GRID_M, centre[1]]);
  });

  it("polyline: Space adds vertices, Enter finishes; Enter adds a vertex below 2", () => {
    const { store, ui, svg } = setup("polyline");
    svg.focus();
    key(svg, "Enter");
    key(svg, "ArrowRight");
    key(svg, "Enter");
    expect(ui.getState().draft).toHaveLength(2);
    key(svg, "ArrowUp");
    key(svg, " ");
    expect(ui.getState().draft).toHaveLength(3);
    key(svg, "Enter");
    const [polyline] = created(store);
    expect(polyline?.geometry).toMatchObject({ kind: "polyline", closed: false });
    expect(polyline?.geometry.kind === "polyline" && polyline.geometry.coords_m).toHaveLength(3);
  });

  it("local frame: Enter marks the origin, arrows drag, Enter marks the +X axis point", () => {
    const { ui, svg, camera } = setup("frame");
    svg.focus();
    const centre: Point2 = [camera().centerX, camera().centerY];
    key(svg, "Enter");
    key(svg, "ArrowRight", { ctrlKey: true });
    expect(ui.getState().hover).not.toBeNull();
    key(svg, "Enter");
    const [origin, axis] = ui.getState().draft;
    close(origin, centre);
    close(axis, [centre[0] + 10 * GRID_M, centre[1]]);
  });

  it("text: Enter places the anchor, the input creates the text and focus returns to the canvas", () => {
    const { store, svg } = setup("text");
    svg.focus();
    key(svg, "Enter");
    const input = screen.getByRole("textbox", { name: TEXT_TOOL_TEXT.input });
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "Calzada" } });
    key(input, "Enter");
    expect(created(store)[0]?.geometry).toMatchObject({ kind: "text", content: "Calzada" });
    expect(document.activeElement).toBe(svg);
  });

  it("leaves the arrows to the view without a drawing tool", () => {
    const { svg, camera } = setup("select");
    svg.focus();
    const before = camera().centerX;
    key(svg, "ArrowRight");
    expect(camera().centerX).not.toBe(before);
    expect(screen.queryByTestId("keyboard-cursor")).toBeNull();
    expect(svg.hasAttribute("aria-describedby")).toBe(false);
  });
});

describe("KeyboardHelp", () => {
  it("opens with ?, lists the shortcuts, traps Tab and closes with Esc", () => {
    const { ui, svg } = setup("line");
    svg.focus();
    key(svg, "?");
    const dialog = screen.getByRole("dialog", { name: KEYBOARD_HELP_TEXT.title });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(within(dialog).getByRole("cell", { name: "Línea" })).toBeTruthy();
    const closeButton = within(dialog).getByRole("button", { name: KEYBOARD_HELP_TEXT.close });
    expect(document.activeElement).toBe(closeButton);
    key(closeButton, "Tab");
    expect(document.activeElement).toBe(closeButton);
    // Tool keys typed in the dialog do not reach the palette.
    key(closeButton, "v");
    expect(ui.getState().activeTool).toBe("line");
    key(closeButton, "Escape");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(svg);
  });

  it("opens from its button and gives focus back to it", () => {
    setup();
    const button = screen.getByRole("button", { name: KEYBOARD_HELP_TEXT.open });
    button.focus();
    act(() => button.click());
    const dialog = screen.getByRole("dialog");
    act(() => within(dialog).getByRole("button", { name: KEYBOARD_HELP_TEXT.close }).click());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(button);
  });
});
