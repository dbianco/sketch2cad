import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EditorWorkspace } from "../EditorWorkspace";
import { CANVAS_TEXT, type Catalog } from "../canvas/EditorCanvas";
import { CSS_PX_PER_MM } from "../canvas/symbols";
import { screenToWorld, type Viewport } from "../canvas/viewport";
import catalogJson from "../fixtures/catalog-default-1.json";
import { PALETTE_TEXT } from "../panels/Palette";
import { createSnapSettingsStore } from "../panels/SnapSettings";
import {
  SYMBOL_PICKER_TEXT,
  createSymbolPickerStore,
  type SymbolPickerState,
} from "../panels/SymbolPicker";
import { createEditorStore } from "../store/entities";
import type { EditorEntity, Point2 } from "../store/types";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";

const DEFAULT_CATALOG = catalogJson as Catalog;
const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const ORIGIN = { left: 40, top: 30 };

function setup(picker: Partial<SymbolPickerState> = {}, tool: ToolId = "symbol", gridM?: number) {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, []);
  const ui = createEditorUiStore({ activeTool: tool });
  const symbols = createSymbolPickerStore(picker);
  const snap = createSnapSettingsStore(null);
  if (gridM !== undefined) {
    snap.getState().setEnabled("grid", true);
    snap.getState().setGridM(gridM);
  }
  let viewport: Viewport | null = null;
  render(
    <EditorWorkspace
      catalog={DEFAULT_CATALOG}
      store={store}
      ui={ui}
      symbols={symbols}
      snap={snap}
      width={800}
      height={600}
      onViewportChange={(next) => {
        viewport = next;
      }}
    />,
  );
  const svg = screen.getByTestId("editor-canvas") as unknown as SVGSVGElement;
  svg.getBoundingClientRect = () => new DOMRect(ORIGIN.left, ORIGIN.top, 800, 600);
  const world = (x: number, y: number): Point2 => {
    if (viewport === null) throw new Error("No viewport yet");
    return screenToWorld(viewport, [x - ORIGIN.left, y - ORIGIN.top]);
  };
  const zoom = (): number => viewport?.scale ?? 0;
  return { store, ui, symbols, svg, world, zoom };
}

function click(target: Element, x: number, y: number) {
  fireEvent.pointerDown(target, { pointerId: 1, button: 0, clientX: x, clientY: y });
  fireEvent.pointerUp(target, { pointerId: 1, button: 0, clientX: x, clientY: y });
}

function entities(store: ReturnType<typeof createEditorStore>): EditorEntity[] {
  const { byId, order } = store.getState().entities;
  return order.map((id) => byId[id] as EditorEntity);
}

afterEach(cleanup);

describe("symbol tool", () => {
  it("places evidence_marker on its default layer with rotation and attributes, queued as create", () => {
    const { store, symbols, svg, world } = setup({
      symbolId: "evidence_marker",
      rotation: "30",
      values: { evidence_number: "4", label: "Vaina" },
    });
    const at = world(300, 250);
    click(svg, 300, 250);

    const [entity] = entities(store);
    expect(entity).toMatchObject({
      type: "symbol",
      layer: "L_EVIDENCE",
      attributes: {},
      geometry: {
        kind: "symbol",
        symbol_id: "evidence_marker",
        rotation_deg: 30,
        scale: 1,
        attributes: { evidence_number: 4, label: "Vaina" },
      },
    });
    if (entity?.geometry.kind !== "symbol") throw new Error("Not a symbol");
    expect(entity.geometry.anchor_m[0]).toBeCloseTo(at[0], 9);
    expect(entity.geometry.anchor_m[1]).toBeCloseTo(at[1], 9);
    const ops = store.getState().queue.ops;
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({
      kind: "create",
      entityId: entity.id,
      payload: { type: "symbol", layer: "L_EVIDENCE", geometry: entity.geometry },
    });
    expect(store.getState().history.past.at(-1)?.name).toBe("add");
    expect(symbols.getState().attempted).toBe(false);
    // Drawn on the canvas as a symbol of the evidence layer.
    const element = svg.querySelector(`[data-entity-id="${entity.id}"]`);
    expect(element?.getAttribute("data-symbol-id")).toBe("evidence_marker");
    expect(element?.closest("[data-layer]")?.getAttribute("data-layer")).toBe("L_EVIDENCE");
  });

  it("snaps the anchor like the drawing tools", () => {
    const { store, svg } = setup({ symbolId: "door", scale: "1,2" }, "symbol", 1);
    click(svg, 333, 271);
    const [entity] = entities(store);
    expect(entity?.layer).toBe("L_ACCESS");
    if (entity?.geometry.kind !== "symbol") throw new Error("Not a symbol");
    expect(entity.geometry.scale).toBe(1.2);
    for (const coordinate of entity.geometry.anchor_m) {
      expect(Number.isInteger(coordinate)).toBe(true);
    }
  });

  it("blocks the placement on a locked default layer and creates nothing", () => {
    const { store, svg } = setup({ symbolId: "tree" });
    fireEvent.click(screen.getByRole("checkbox", { name: CANVAS_TEXT.lock("L_SYMBOLS") }));
    click(svg, 300, 250);
    expect(entities(store)).toEqual([]);
    expect(store.getState().queue.ops).toEqual([]);
    expect(screen.getByRole("alert").textContent).toBe(
      SYMBOL_PICKER_TEXT.layerNotDrawable("L_SYMBOLS"),
    );
  });

  it("asks for a symbol when none is chosen", () => {
    const { store, svg } = setup();
    click(svg, 300, 250);
    expect(entities(store)).toEqual([]);
    expect(screen.getByRole("alert").textContent).toBe(SYMBOL_PICKER_TEXT.noSymbol);
  });

  it("falls back to the palette's active layer when the default layer is not in the catalog", () => {
    const catalog: Catalog = {
      ...DEFAULT_CATALOG,
      symbols: DEFAULT_CATALOG.symbols.map((symbol) =>
        symbol.symbol_id === "tree" ? { ...symbol, default_layer: "L_GONE" } : symbol,
      ),
    };
    const store = createEditorStore();
    store.getState().load(REVISION_ID, []);
    render(
      <EditorWorkspace
        catalog={catalog}
        store={store}
        ui={createEditorUiStore({ activeTool: "symbol" })}
        symbols={createSymbolPickerStore({ symbolId: "tree" })}
        snap={createSnapSettingsStore(null)}
        width={800}
        height={600}
      />,
    );
    fireEvent.change(screen.getByRole("combobox", { name: PALETTE_TEXT.layer }), {
      target: { value: "L_ANNOTATIONS" },
    });
    click(screen.getByTestId("editor-canvas"), 300, 250);
    expect(entities(store)[0]?.layer).toBe("L_ANNOTATIONS");
  });

  it("shows a ghost of the chosen symbol under the pointer, at its screen size", () => {
    const { svg, symbols, zoom } = setup({ symbolId: "evidence_marker" });
    expect(screen.queryByTestId("symbol-ghost")).toBeNull();
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 300, clientY: 250 });
    const ghost = screen.getByTestId("symbol-ghost");
    expect(ghost.getAttribute("opacity")).toBe("0.5");
    const scale = Number(/scale\(([^)]+)\)/.exec(ghost.getAttribute("transform") ?? "")?.[1]);
    expect(scale * 100 * zoom()).toBeCloseTo(6 * CSS_PX_PER_MM, 6);
    act(() => symbols.getState().setRotation("90"));
    expect(screen.getByTestId("symbol-ghost").getAttribute("transform")).toContain("rotate(-90)");
  });

  it("leaves the tool with Esc and is chosen with the S shortcut", () => {
    const { ui } = setup({}, "select");
    fireEvent.keyDown(document.body, { key: "s" });
    expect(ui.getState().activeTool).toBe("symbol");
    expect(screen.getByRole("complementary", { name: SYMBOL_PICKER_TEXT.panel })).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(ui.getState().activeTool).toBe("select");
    expect(screen.queryByRole("complementary", { name: SYMBOL_PICKER_TEXT.panel })).toBeNull();
  });
});
