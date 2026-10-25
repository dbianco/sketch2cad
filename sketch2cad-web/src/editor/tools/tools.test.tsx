import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EditorWorkspace } from "../EditorWorkspace";
import { CANVAS_TEXT, type Catalog } from "../canvas/EditorCanvas";
import { screenToWorld, type Viewport } from "../canvas/viewport";
import { PALETTE_TEXT, TOOL_BUTTONS } from "../panels/Palette";
import { createEditorStore } from "../store/entities";
import type { EditorEntity, EntityOut, Geometry, Point2 } from "../store/types";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";
import { TEXT_HEIGHT_MM_PAPER, TEXT_TOOL_TEXT } from "./PointTextTools";
import { SELECT_TEXT } from "./SelectTool";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
/** Client position of the canvas's top-left corner (stubbed layout). */
const ORIGIN = { left: 40, top: 30 };

const CATALOG: Catalog = {
  id: "00000000-0000-4000-8000-0000000000c1",
  version: "default-1",
  layers: [
    { name: "L_STRUCTURE", description: "Muros", aci_color: 7 },
    { name: "L_EVIDENCE", description: "Indicios", aci_color: 1 },
    { name: "L_TEXT", description: "Etiquetas", aci_color: 7 },
    { name: "L_SHEET", description: "Cajetín", aci_color: 7 },
  ],
  symbols: [],
};

function entityOut(id: string, layer: string, geometry: Geometry): EntityOut {
  return {
    id,
    type: geometry.kind,
    layer,
    geometry,
    attributes: {},
    confidence: null,
    constraint: null,
    created_by: null,
    deleted_at: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "none",
    model_version: null,
    review_status: "accepted",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "manual" },
    updated_at: "2026-10-25T00:00:00Z",
  };
}

const ENTITIES: EntityOut[] = [
  entityOut("wall", "L_STRUCTURE", {
    kind: "line",
    coords_m: [
      [0, 0],
      [10, 0],
    ],
  }),
  entityOut("mark", "L_EVIDENCE", { kind: "point", coords_m: [[2, 3]] }),
];

function setup(tool: ToolId = "select", entities: EntityOut[] = ENTITIES) {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, entities);
  const ui = createEditorUiStore({ activeTool: tool });
  let viewport: Viewport | null = null;
  const result = render(
    <EditorWorkspace
      catalog={CATALOG}
      store={store}
      ui={ui}
      width={800}
      height={600}
      onViewportChange={(next) => {
        viewport = next;
      }}
    />,
  );
  const svg = screen.getByTestId("editor-canvas") as unknown as SVGSVGElement;
  svg.getBoundingClientRect = () => new DOMRect(ORIGIN.left, ORIGIN.top, 800, 600);
  /** World point under a client position, with the camera of the last render. */
  const world = (x: number, y: number): Point2 => {
    if (viewport === null) throw new Error("No viewport yet");
    return screenToWorld(viewport, [x - ORIGIN.left, y - ORIGIN.top]);
  };
  return { store, ui, svg, world, ...result };
}

function press(target: Element, x: number, y: number, init: Record<string, unknown> = {}) {
  fireEvent.pointerDown(target, { pointerId: 1, button: 0, clientX: x, clientY: y, ...init });
}
function release(target: Element, x: number, y: number) {
  fireEvent.pointerUp(target, { pointerId: 1, button: 0, clientX: x, clientY: y });
}
function click(target: Element, x: number, y: number, init: Record<string, unknown> = {}) {
  press(target, x, y, init);
  release(target, x, y);
}
function key(name: string, target: Element = document.body) {
  fireEvent.keyDown(target, { key: name });
}
function chooseLayer(layer: string) {
  fireEvent.change(screen.getByRole("combobox", { name: PALETTE_TEXT.layer }), {
    target: { value: layer },
  });
}
function created(store: ReturnType<typeof createEditorStore>): EditorEntity[] {
  const { byId, order } = store.getState().entities;
  return order.filter((id) => id.startsWith("local-")).map((id) => byId[id] as EditorEntity);
}
function lastOp(store: ReturnType<typeof createEditorStore>) {
  return store.getState().queue.ops.at(-1);
}

afterEach(cleanup);

describe("drawing tools", () => {
  it("line: two clicks create a line on the layer chosen in the palette, through the store", () => {
    const { store, svg, world } = setup("line", []);
    chooseLayer("L_EVIDENCE");
    const a = world(100, 100);
    click(svg, 100, 100);
    const b = world(300, 200);
    click(svg, 300, 200);

    const [line] = created(store);
    expect(line?.type).toBe("line");
    expect(line?.layer).toBe("L_EVIDENCE");
    expect(line?.geometry).toEqual({ kind: "line", coords_m: [a, b] });
    expect(lastOp(store)).toMatchObject({
      kind: "create",
      payload: { type: "line", layer: "L_EVIDENCE", geometry: { kind: "line", coords_m: [a, b] } },
    });
    expect(store.getState().history.past.at(-1)?.name).toBe("add");
  });

  it("line: a press-drag-release creates a line; a second click on the start creates nothing", () => {
    const { store, svg, world } = setup("line", []);
    const a = world(100, 100);
    press(svg, 100, 100);
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 250, clientY: 120 });
    expect(screen.getByTestId("draft-preview")).toBeDefined();
    const b = world(250, 120);
    release(svg, 250, 120);
    expect(created(store).map((e) => e.geometry)).toEqual([{ kind: "line", coords_m: [a, b] }]);
    expect(created(store)[0]?.layer).toBe("L_STRUCTURE");

    click(svg, 400, 400);
    click(svg, 400, 400);
    expect(created(store)).toHaveLength(1);
    key("Escape");
    expect(screen.queryByTestId("draft-preview")).toBeNull();
  });

  it("polyline: clicks add vertices, Backspace removes the last one and Enter finishes it open", () => {
    const { store, svg, world } = setup("polyline", []);
    chooseLayer("L_TEXT");
    const points = [world(100, 100), world(200, 100), world(200, 200)];
    click(svg, 100, 100);
    click(svg, 200, 100);
    click(svg, 200, 200);
    click(svg, 500, 500);
    key("Backspace");
    key("Enter");

    expect(created(store).map((e) => [e.type, e.layer, e.geometry])).toEqual([
      ["polyline", "L_TEXT", { kind: "polyline", coords_m: points, closed: false }],
    ]);
    expect(lastOp(store)).toMatchObject({ kind: "create", payload: { type: "polyline" } });
  });

  it("polyline: Esc cancels, Enter needs two vertices, C and Shift + click close it", () => {
    const { store, svg } = setup("polyline", []);
    click(svg, 100, 100);
    click(svg, 200, 100);
    key("Escape");
    key("Enter");
    expect(created(store)).toHaveLength(0);

    click(svg, 100, 100);
    key("Enter");
    key("c");
    expect(created(store)).toHaveLength(0);
    click(svg, 200, 100);
    key("C");
    expect(created(store)).toHaveLength(0);
    click(svg, 200, 200);
    key("C");
    expect(created(store)[0]?.geometry).toMatchObject({ closed: true });
    expect((created(store)[0]?.geometry as { coords_m: Point2[] }).coords_m).toHaveLength(3);

    click(svg, 100, 100);
    click(svg, 200, 100);
    click(svg, 200, 200, { shiftKey: true });
    expect(created(store)[1]?.geometry).toMatchObject({ kind: "polyline", closed: true });
    expect((created(store)[1]?.geometry as { coords_m: Point2[] }).coords_m).toHaveLength(3);
  });

  it("polyline: a double-click finishes it without a repeated vertex", () => {
    const { store, svg } = setup("polyline", []);
    click(svg, 100, 100);
    click(svg, 300, 100);
    click(svg, 300, 100);
    fireEvent.doubleClick(svg, { clientX: 300, clientY: 100 });
    const geometry = created(store)[0]?.geometry as { coords_m: Point2[]; closed: boolean };
    expect(geometry.coords_m).toHaveLength(2);
    expect(geometry.closed).toBe(false);
  });

  it("point: a click creates a point on the chosen layer", () => {
    const { store, svg, world } = setup("point", []);
    chooseLayer("L_EVIDENCE");
    const p = world(150, 250);
    click(svg, 150, 250);
    expect(created(store).map((e) => [e.type, e.layer, e.geometry])).toEqual([
      ["point", "L_EVIDENCE", { kind: "point", coords_m: [p] }],
    ]);
    expect(lastOp(store)).toMatchObject({ kind: "create", payload: { type: "point" } });
  });

  it("text: a click opens a labelled input; Enter creates the text, blank or Esc creates nothing", () => {
    const { store, svg, world } = setup("text", []);
    chooseLayer("L_TEXT");
    const anchor = world(120, 80);
    click(svg, 120, 80);
    const input = screen.getByRole("textbox", { name: TEXT_TOOL_TEXT.input });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(created(store)).toHaveLength(0);
    // Tool shortcuts typed in the input are text, not tool changes.
    fireEvent.keyDown(input, { key: "L" });
    expect(screen.getByRole("button", { name: "Texto" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.change(input, { target: { value: "  Cocina " } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(created(store).map((e) => [e.type, e.layer, e.geometry])).toEqual([
      [
        "text",
        "L_TEXT",
        {
          kind: "text",
          anchor_m: anchor,
          content: "Cocina",
          height_mm_paper: TEXT_HEIGHT_MM_PAPER,
          rotation_deg: 0,
        },
      ],
    ]);
    expect(lastOp(store)).toMatchObject({ kind: "create", payload: { type: "text" } });
    expect(screen.queryByRole("textbox", { name: TEXT_TOOL_TEXT.input })).toBeNull();

    click(svg, 300, 300);
    fireEvent.change(screen.getByRole("textbox", { name: TEXT_TOOL_TEXT.input }), {
      target: { value: "Baño" },
    });
    fireEvent.keyDown(screen.getByRole("textbox", { name: TEXT_TOOL_TEXT.input }), {
      key: "Escape",
    });
    expect(created(store)).toHaveLength(1);
  });

  it("does not draw on a locked or hidden layer and says why", () => {
    const { store, svg } = setup("point", []);
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: CANVAS_TEXT.lock("L_STRUCTURE") }));
    click(svg, 100, 100);
    expect(created(store)).toHaveLength(0);
    expect(screen.getByRole("status").textContent).toBe(PALETTE_TEXT.notDrawable);

    chooseLayer("L_EVIDENCE");
    fireEvent.click(screen.getByRole("checkbox", { name: CANVAS_TEXT.show("L_EVIDENCE") }));
    click(svg, 100, 100);
    expect(created(store)).toHaveLength(0);
  });

  it("offers only editable catalog layers in the layer picker (no L_SHEET)", () => {
    setup();
    const options = [
      ...screen.getByRole("combobox", { name: PALETTE_TEXT.layer }).querySelectorAll("option"),
    ].map((option) => option.value);
    expect(options).toEqual(["L_STRUCTURE", "L_EVIDENCE", "L_TEXT"]);
  });
});

describe("select tool", () => {
  function entity(container: HTMLElement, id: string): Element {
    const element = container.querySelector(`[data-entity-id="${id}"]`);
    if (element === null) throw new Error(`No element for ${id}`);
    return element;
  }

  it("selects with a click, adds with Shift + click and deletes with the Delete key", () => {
    const { store, container } = setup();
    click(entity(container, "wall"), 10, 10);
    expect(store.getState().selection).toEqual(["wall"]);
    click(entity(container, "mark"), 20, 20, { shiftKey: true });
    expect(store.getState().selection).toEqual(["wall", "mark"]);
    expect(screen.getByText(SELECT_TEXT.count(2))).toBeDefined();

    key("Delete");

    const { byId } = store.getState().entities;
    expect([byId.wall?.deleted, byId.mark?.deleted]).toEqual([true, true]);
    expect(store.getState().queue.ops.map((op) => [op.kind, op.entityId])).toEqual([
      ["delete", "wall"],
      ["delete", "mark"],
    ]);
    expect(store.getState().selection).toEqual([]);
  });

  it("Backspace deletes too, Shift + click toggles off and Esc clears the selection", () => {
    const { store, container } = setup();
    click(entity(container, "wall"), 10, 10);
    click(entity(container, "mark"), 20, 20, { shiftKey: true });
    click(entity(container, "mark"), 20, 20, { shiftKey: true });
    expect(store.getState().selection).toEqual(["wall"]);
    key("Escape");
    expect(store.getState().selection).toEqual([]);

    click(entity(container, "mark"), 20, 20);
    key("Backspace");
    expect(store.getState().entities.byId.mark?.deleted).toBe(true);
    expect(store.getState().entities.byId.wall?.deleted).toBe(false);
  });

  it("reclassifies the selection with the layer dropdown", () => {
    const { store, container } = setup();
    click(entity(container, "wall"), 10, 10);
    const dropdown = screen.getByRole<HTMLSelectElement>("combobox", { name: SELECT_TEXT.layer });
    expect(dropdown.value).toBe("L_STRUCTURE");

    fireEvent.change(dropdown, { target: { value: "L_TEXT" } });

    expect(store.getState().entities.byId.wall?.layer).toBe("L_TEXT");
    expect(lastOp(store)).toMatchObject({
      kind: "patch",
      entityId: "wall",
      operation: "reclassify",
      payload: { layer: "L_TEXT" },
    });
    // The entity is now drawn in the group of its new layer.
    expect(entity(container, "wall").closest("g[data-layer]")?.getAttribute("data-layer")).toBe(
      "L_TEXT",
    );
  });

  it("deletes the selection with the panel button", () => {
    const { store, container } = setup();
    click(entity(container, "mark"), 20, 20);
    fireEvent.click(screen.getByRole("button", { name: SELECT_TEXT.remove }));
    expect(store.getState().entities.byId.mark?.deleted).toBe(true);
  });

  it("ignores entities on locked layers", () => {
    const { store, container } = setup();
    fireEvent.click(screen.getByRole("checkbox", { name: CANVAS_TEXT.lock("L_EVIDENCE") }));
    click(entity(container, "mark"), 20, 20);
    expect(store.getState().selection).toEqual([]);
  });
});

describe("panning with tools", () => {
  function drag(svg: Element, button: number) {
    fireEvent.pointerDown(svg, { pointerId: 7, button, clientX: 300, clientY: 300 });
    fireEvent.pointerMove(svg, { pointerId: 7, clientX: 360, clientY: 330 });
    fireEvent.pointerUp(svg, { pointerId: 7, button, clientX: 360, clientY: 330 });
  }

  it("pans on a background drag with Select, and only with the middle button or Space while drawing", () => {
    const { svg, ui, store } = setup();
    let before = svg.getAttribute("viewBox");
    drag(svg, 0);
    expect(svg.getAttribute("viewBox")).not.toBe(before);

    act(() => ui.getState().setTool("polyline"));
    before = svg.getAttribute("viewBox");
    drag(svg, 0);
    expect(svg.getAttribute("viewBox")).toBe(before);
    expect(ui.getState().draft).toHaveLength(1);
    key("Escape");

    drag(svg, 1);
    expect(svg.getAttribute("viewBox")).not.toBe(before);

    before = svg.getAttribute("viewBox");
    fireEvent.keyDown(window, { key: " " });
    drag(svg, 0);
    fireEvent.keyUp(window, { key: " " });
    expect(svg.getAttribute("viewBox")).not.toBe(before);
    expect(ui.getState().draft).toHaveLength(0);
    expect(created(store)).toHaveLength(0);
  });
});

describe("palette", () => {
  function tabbable(root: ParentNode): HTMLElement[] {
    return [...root.querySelectorAll<HTMLElement>("button, input, select, [tabindex]")].filter(
      (element) => !element.hasAttribute("disabled") && element.tabIndex >= 0,
    );
  }

  it("labels every tool button and puts them in the Tab order, one after another", () => {
    const { container } = setup();
    const buttons = TOOL_BUTTONS.map((tool) => screen.getByRole("button", { name: tool.label }));
    expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
      "Seleccionar",
      "Editar vértices",
      "Línea",
      "Polilínea",
      "Punto",
      "Texto",
    ]);
    for (const [index, button] of buttons.entries()) {
      expect(button.getAttribute("title")).toBe(
        `${TOOL_BUTTONS[index]?.label ?? ""} (${TOOL_BUTTONS[index]?.shortcut ?? ""})`,
      );
    }
    // No positive tabindex anywhere: the Tab order is the document order of the tabbables.
    const order = tabbable(container);
    expect(order.every((element) => element.tabIndex === 0)).toBe(true);
    const start = order.indexOf(buttons[0] as HTMLElement);
    expect(order.slice(start, start + buttons.length)).toEqual(buttons);
    // Tab through: focusing each stop in order lands on the next palette button.
    for (const [index, button] of buttons.entries()) {
      order[start + index]?.focus();
      expect(document.activeElement).toBe(button);
    }
    expect(order[start + buttons.length]).toBe(
      screen.getByRole("combobox", { name: PALETTE_TEXT.layer }),
    );
  });

  it("switches tools with the shortcuts and with the buttons", () => {
    const { ui } = setup();
    const pressed = () =>
      TOOL_BUTTONS.filter(
        (tool) =>
          screen.getByRole("button", { name: tool.label }).getAttribute("aria-pressed") === "true",
      ).map((tool) => tool.id);
    expect(pressed()).toEqual(["select"]);
    for (const [shortcut, tool] of [
      ["l", "line"],
      ["P", "polyline"],
      ["o", "point"],
      ["t", "text"],
      ["e", "vertex"],
      ["v", "select"],
    ] as const) {
      key(shortcut);
      expect(ui.getState().activeTool).toBe(tool);
      expect(pressed()).toEqual([tool]);
    }
    fireEvent.keyDown(document.body, { key: "l", ctrlKey: true });
    expect(ui.getState().activeTool).toBe("select");
    fireEvent.click(screen.getByRole("button", { name: "Polilínea" }));
    expect(ui.getState().activeTool).toBe("polyline");
  });

  it("ignores shortcuts typed in a form field", () => {
    const { ui } = setup();
    const picker = screen.getByRole("combobox", { name: PALETTE_TEXT.layer });
    fireEvent.keyDown(picker, { key: "l" });
    expect(ui.getState().activeTool).toBe("select");
  });

  it("drops the drawing in progress when the tool changes", () => {
    const { ui, svg } = setup("polyline", []);
    click(svg, 100, 100);
    expect(ui.getState().draft).toHaveLength(1);
    key("L");
    expect(ui.getState().draft).toHaveLength(0);
  });
});
