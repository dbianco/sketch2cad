import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EditorWorkspace } from "../EditorWorkspace";
import { CANVAS_TEXT, dimensionDiscrepancy, type Catalog } from "../canvas/EditorCanvas";
import type { Viewport } from "../canvas/viewport";
import catalogJson from "../fixtures/catalog-default-1.json";
import { createSnapSettingsStore } from "../panels/SnapSettings";
import { SELECT_TEXT } from "./SelectTool";
import { createPlanStore } from "../plan/planStore";
import { createEditorStore, toApiRequest } from "../store/entities";
import type { EntityOut, Geometry, Point2 } from "../store/types";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";
import { DISTANCE_MESSAGE } from "../util/parseDistance";
import { DIMENSION_TEXT, parseOffset } from "./DimensionTool";

const CATALOG = catalogJson as Catalog;
const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const LINE_ID = "00000000-0000-4000-8000-0000000000e1";
const DIM_ID = "00000000-0000-4000-8000-0000000000d1";
const ORIGIN = { left: 40, top: 30 };

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
    discrepancy: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "none",
    model_version: null,
    review_status: "accepted",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "manual" },
    updated_at: "2026-10-27T00:00:00Z",
  };
}

const LINE = entityOut(LINE_ID, "L_STRUCTURE", { kind: "line", coords_m: [[0, 0], [4, 0]] });

interface SetupOptions {
  readOnly?: boolean;
  tool?: ToolId;
  entities?: EntityOut[];
  locked?: boolean;
}

function setup(options: SetupOptions = {}) {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, options.entities ?? [LINE]);
  const ui = createEditorUiStore({
    activeTool: options.tool ?? "dimension",
    layerFlags: options.locked === true ? { L_DIMENSIONS: { visible: true, locked: true } } : {},
  });
  const plan = createPlanStore({ revisionId: REVISION_ID, readOnly: options.readOnly ?? false });
  let viewport: Viewport | null = null;
  render(
    <EditorWorkspace
      catalog={CATALOG}
      store={store}
      ui={ui}
      plan={plan}
      snap={createSnapSettingsStore(null)}
      width={800}
      height={600}
      onViewportChange={(next) => {
        viewport = next;
      }}
    />,
  );
  const svg = screen.getByTestId("editor-canvas") as unknown as SVGSVGElement;
  svg.getBoundingClientRect = () => new DOMRect(ORIGIN.left, ORIGIN.top, 800, 600);
  /** Click (press and release) at a world point. */
  const click = (p: Point2) => {
    if (viewport === null) throw new Error("No viewport yet");
    const { centerX, centerY, scale, width, height } = viewport;
    const clientX = (p[0] - centerX) * scale + width / 2 + ORIGIN.left;
    const clientY = (centerY - p[1]) * scale + height / 2 + ORIGIN.top;
    fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX, clientY });
    fireEvent.pointerUp(svg, { pointerId: 1, button: 0, clientX, clientY });
  };
  /** The request the head of the queue would send. */
  const request = (index = 0) => {
    const op = store.getState().queue.ops[index];
    if (op === undefined) throw new Error("Nothing queued");
    return toApiRequest(op, { revisionId: REVISION_ID, serverIds: store.getState().serverIds });
  };
  /** Geometry of the dimension the head of the queue would create. */
  const created = () => {
    const descriptor = request();
    if (descriptor.method !== "post") throw new Error("The head of the queue is not a create");
    return descriptor.options.body.entities[0]?.geometry;
  };
  return { store, ui, click, request, created };
}

function valueField(): HTMLInputElement {
  return screen.getByRole("textbox", { name: DIMENSION_TEXT.value });
}

function offsetField(): HTMLInputElement {
  return screen.getByRole("textbox", { name: DIMENSION_TEXT.offset });
}

function submit(value: string, offset?: string) {
  fireEvent.change(valueField(), { target: { value } });
  if (offset !== undefined) fireEvent.change(offsetField(), { target: { value: offset } });
  act(() => {
    fireEvent.click(screen.getByRole("button", { name: DIMENSION_TEXT.create }));
  });
}

afterEach(cleanup);

describe("dimension tool", () => {
  it("two clicks and a value queue a dimension on L_DIMENSIONS with the default offset", () => {
    const { store, click, request } = setup();
    expect(screen.getByText(DIMENSION_TEXT.pickFirst)).toBeTruthy();
    click([0, 0]);
    expect(screen.getByText(DIMENSION_TEXT.pickSecond)).toBeTruthy();
    click([0, 3]);
    expect(screen.getByText(DIMENSION_TEXT.measured(3))).toBeTruthy();
    // 5 mm of paper at the default 1:100.
    expect(offsetField().value).toBe("0,50");

    submit("3.05");

    expect(request()).toEqual({
      method: "post",
      path: "/api/v1/revisions/{revision_id}/entities",
      options: {
        path: { revision_id: REVISION_ID },
        body: {
          entities: [
            {
              type: "dimension",
              layer: "L_DIMENSIONS",
              attributes: {},
              geometry: {
                kind: "dimension",
                p1_m: [0, 0],
                p2_m: [0, 3],
                offset_m: 0.5,
                declared_value_m: 3.05,
              },
            },
          ],
        },
      },
    });
    expect(screen.getByText(DIMENSION_TEXT.done(3.05)).getAttribute("role")).toBe("status");
    expect(screen.queryByRole("form", { name: DIMENSION_TEXT.form })).toBeNull();
    // Undo removes it like any drawn entity.
    act(() => store.getState().undo());
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("takes decimal commas and a negative offset", () => {
    const { click, created } = setup();
    click([0, 0]);
    click([0, 3]);

    submit("4,20", "-0,75");

    expect(created()).toMatchObject({ declared_value_m: 4.2, offset_m: -0.75 });
  });

  it.each(["0", "abc", "-1", ""])("rejects the value %j and queues nothing", (value) => {
    const { store, click } = setup();
    click([0, 0]);
    click([0, 3]);

    submit(value);

    expect(screen.getByRole("alert").textContent).toBe(DISTANCE_MESSAGE);
    expect(store.getState().queue.ops).toEqual([]);
    expect(valueField().getAttribute("aria-invalid")).toBe("true");
  });

  it("rejects an offset that is not a number", () => {
    const { store, click } = setup();
    click([0, 0]);
    click([0, 3]);

    submit("3", "a lot");

    expect(screen.getByRole("alert").textContent).toBe(DIMENSION_TEXT.offsetInvalid);
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("a click on a segment takes its two ends", () => {
    const { click, created } = setup();
    click([2, 0.02]);
    expect(screen.getByTestId("dimension-ends")).toBeTruthy();
    expect(screen.getByText(DIMENSION_TEXT.measured(4))).toBeTruthy();

    submit("4");

    expect(created()).toMatchObject({ p1_m: [0, 0], p2_m: [4, 0] });
  });

  it("a second click on the first point asks for another one; Esc drops the points", () => {
    const { ui, click } = setup();
    click([0, 3]);
    click([0, 3]);
    expect(screen.getByRole("alert").textContent).toBe(DIMENSION_TEXT.samePoint);
    expect(ui.getState().draft).toHaveLength(1);

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(ui.getState().draft).toEqual([]);
    expect(screen.getByText(DIMENSION_TEXT.pickFirst)).toBeTruthy();
  });

  it("says why when L_DIMENSIONS is locked", () => {
    const { store, click } = setup({ locked: true });
    click([0, 0]);
    click([0, 3]);

    submit("3");

    expect(screen.getByRole("alert").textContent).toBe(DIMENSION_TEXT.layerLocked);
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("is disabled without the lease: no palette button, no shortcut, no dimension", () => {
    const { store, ui, click } = setup({ readOnly: true, tool: "select" });
    const button = screen.getByRole("button", { name: "Cota" });
    expect(button.hasAttribute("disabled")).toBe(true);
    fireEvent.keyDown(document.body, { key: "a" });
    expect(ui.getState().activeTool).toBe("select");
    click([0, 0]);
    click([0, 3]);
    expect(screen.queryByRole("textbox", { name: DIMENSION_TEXT.value })).toBeNull();
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("the A shortcut and the palette button select the tool", () => {
    const { ui } = setup({ tool: "select" });
    fireEvent.keyDown(document.body, { key: "a" });
    expect(ui.getState().activeTool).toBe("dimension");
    expect(screen.getByRole("button", { name: "Cota" }).getAttribute("aria-keyshortcuts")).toBe("A");
  });
});

describe("dimensions on the canvas and in the selection panel", () => {
  const dimension = (declared: number | null, x2: number) =>
    entityOut(DIM_ID, "L_DIMENSIONS", {
      kind: "dimension",
      p1_m: [0, 0],
      p2_m: [x2, 0],
      offset_m: 0.5,
      declared_value_m: declared,
      measured_value_m: x2,
    });

  it("shows the declared value, and the measured one with a warning beyond the tolerance", () => {
    setup({ tool: "select", entities: [dimension(4.2, 4.24)] });
    const group = document.querySelector(`[data-entity-id="${DIM_ID}"]`);
    expect(group?.textContent).toContain("4,20 m");
    expect(group?.getAttribute("data-discrepancy")).toBe("true");
    const warning = screen.getByTestId("dimension-discrepancy");
    expect(warning.textContent).toContain(CANVAS_TEXT.measured("4,24 m"));
    expect(warning.querySelector("title")?.textContent).toBe(
      CANVAS_TEXT.discrepancy("4,20 m", "4,24 m"),
    );
  });

  it("follows the server rule: 4.215 is within 0.02 of 4.20, plain measurements never warn", () => {
    expect(dimensionDiscrepancy(4.2, 4.24)).toBe(true);
    expect(dimensionDiscrepancy(4.2, 4.215)).toBe(false);
    expect(dimensionDiscrepancy(4.2, 4.22)).toBe(false);
    expect(dimensionDiscrepancy(null, 9)).toBe(false);
    setup({ tool: "select", entities: [dimension(4.2, 4.215)] });
    expect(screen.queryByTestId("dimension-discrepancy")).toBeNull();
  });

  it("selects, edits the declared value and deletes a dimension", () => {
    const { store, request } = setup({ tool: "select", entities: [dimension(4.2, 4.24)] });
    act(() => store.getState().select([DIM_ID]));
    const field = screen.getByRole("textbox", { name: SELECT_TEXT.declared }) as HTMLInputElement;
    expect(field.value).toBe("4,20");

    fireEvent.change(field, { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: SELECT_TEXT.saveDeclared }));
    expect(screen.getByRole("alert").textContent).toBe(DISTANCE_MESSAGE);
    expect(store.getState().queue.ops).toEqual([]);

    fireEvent.change(field, { target: { value: "4,24" } });
    fireEvent.click(screen.getByRole("button", { name: SELECT_TEXT.saveDeclared }));
    expect(request()).toMatchObject({
      method: "patch",
      options: {
        path: { entity_id: DIM_ID },
        body: { operation: "relabel", geometry: { kind: "dimension", declared_value_m: 4.24 } },
      },
    });
    expect(screen.queryByTestId("dimension-discrepancy")).toBeNull();

    fireEvent.keyDown(document.body, { key: "Delete" });
    expect(request(1)).toMatchObject({ method: "delete", options: { path: { entity_id: DIM_ID } } });
    expect(store.getState().entities.byId[DIM_ID]?.deleted).toBe(true);
  });
});

describe("parseOffset", () => {
  it.each([
    ["0,5", 0.5],
    ["-0.75", -0.75],
    [" +1 ", 1],
    ["0", 0],
  ])("reads %j as %d", (text, value) => {
    expect(parseOffset(text)).toBe(value);
  });

  it.each(["", "abc", "1e3", "--1", "1,2,3"])("rejects %j", (text) => {
    expect(parseOffset(text)).toBeNull();
  });
});
