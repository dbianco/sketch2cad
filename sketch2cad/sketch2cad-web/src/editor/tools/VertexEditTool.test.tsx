import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { EditorWorkspace } from "../EditorWorkspace";
import type { Catalog } from "../canvas/EditorCanvas";
import { worldToScreen, type Viewport } from "../canvas/viewport";
import { SNAP_TEXT, createSnapSettingsStore } from "../panels/SnapSettings";
import { createEditorStore, toApiRequest } from "../store/entities";
import { flushQueue } from "../store/sync";
import type { EntityOut, Geometry, Point2 } from "../store/types";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";
import { VERTEX_TEXT } from "./VertexEditTool";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const ORIGIN = { left: 40, top: 30 };

const CATALOG: Catalog = {
  id: "00000000-0000-4000-8000-0000000000c1",
  version: "default-1",
  layers: [
    { name: "L_STRUCTURE", description: "Muros", aci_color: 7 },
    { name: "L_SHEET", description: "Cajetín", aci_color: 7 },
  ],
  symbols: [],
};

function entityOut(id: string, geometry: Geometry): EntityOut {
  return {
    id,
    type: geometry.kind,
    layer: "L_STRUCTURE",
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
    updated_at: "2026-10-25T00:00:00Z",
  };
}

const ENTITIES: EntityOut[] = [
  entityOut("wall", {
    kind: "line",
    coords_m: [
      [0, 0],
      [10, 0],
    ],
  }),
  entityOut("post", {
    kind: "line",
    coords_m: [
      [20, 5],
      [20, 8],
    ],
  }),
];

function setup(tool: ToolId = "vertex") {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, ENTITIES);
  const ui = createEditorUiStore({ activeTool: tool });
  const snap = createSnapSettingsStore(null);
  let viewport: Viewport | null = null;
  const result = render(
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
  svg.getBoundingClientRect = () => new DOMRect(ORIGIN.left, ORIGIN.top, 800, 600);
  /** Client position of a world point with the camera of the last render. */
  const at = (point: Point2) => {
    if (viewport === null) throw new Error("No viewport yet");
    const [x, y] = worldToScreen(viewport, point);
    return { clientX: x + ORIGIN.left, clientY: y + ORIGIN.top };
  };
  const pointer = (type: "down" | "move" | "up", target: Element, point: Point2) => {
    const init = { pointerId: 1, button: 0, ...at(point) };
    if (type === "down") fireEvent.pointerDown(target, init);
    else if (type === "move") fireEvent.pointerMove(target, init);
    else fireEvent.pointerUp(target, init);
  };
  /** Select the wall by clicking it, as the user would. */
  const selectWall = () => {
    const wall = result.container.querySelector('[data-entity-id="wall"]') as Element;
    pointer("down", wall, [5, 0]);
    pointer("up", wall, [5, 0]);
  };
  const drag = (from: Point2, path: Point2[]) => {
    pointer("down", svg, from);
    for (const point of path) pointer("move", svg, point);
    pointer("up", svg, path.at(-1) ?? from);
  };
  const wallCoords = () =>
    (store.getState().entities.byId.wall?.geometry as { coords_m: Point2[] }).coords_m;
  return { store, ui, snap, svg, at, pointer, selectWall, drag, wallCoords, ...result };
}

afterEach(cleanup);

describe("vertex edit tool", () => {
  it("selecting an entity shows a labelled, focusable handle per vertex", () => {
    const { store, selectWall } = setup();
    expect(screen.queryByRole("group", { name: VERTEX_TEXT.handles })).toBeNull();
    selectWall();
    expect(store.getState().selection).toEqual(["wall"]);
    const handles = [VERTEX_TEXT.handle(0, 2), VERTEX_TEXT.handle(1, 2)].map((name) =>
      screen.getByRole("button", { name }),
    );
    for (const handle of handles) {
      handle.focus();
      expect(document.activeElement).toBe(handle);
    }
  });

  it("orthogonal snap: queues a PATCH with operation snap_orthogonal, sent through the client", async () => {
    const { store, svg, pointer, selectWall, wallCoords } = setup();
    selectWall();
    pointer("down", svg, [10, 0]);
    pointer("move", svg, [13, 0.05]);
    expect(screen.getByTestId("snap-indicator").textContent).toBe(SNAP_TEXT.applied.orthogonal);
    expect(screen.getByTestId("vertex-drag-preview")).toBeDefined();
    pointer("up", svg, [13, 0.05]);

    const [start, end] = wallCoords();
    expect(start).toEqual([0, 0]);
    expect(end?.[1]).toBe(0);
    expect(end?.[0]).toBeCloseTo(13, 1);
    expect(store.getState().history.past.map((entry) => entry.name)).toEqual(["snap_orthogonal"]);
    const op = store.getState().queue.ops.at(-1);
    if (op === undefined) throw new Error("Nothing queued");
    const request = toApiRequest(op, { revisionId: REVISION_ID, serverIds: {} });
    expect(request).toMatchObject({
      method: "patch",
      path: "/api/v1/entities/{entity_id}",
      options: { path: { entity_id: "wall" }, body: { operation: "snap_orthogonal" } },
    });

    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(JSON.stringify(ENTITIES[0]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const result = await flushQueue(createApiClient({ fetch: fetchMock }), store, {
      revisionId: REVISION_ID,
    });
    expect(result).toEqual({ status: "drained", sent: 1 });
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("/api/v1/entities/wall");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({
      geometry: { kind: "line", coords_m: [start, end] },
      operation: "snap_orthogonal",
    });
  });

  it("a drag without snapping commits one move_vertex entry", () => {
    const { store, drag, selectWall, wallCoords } = setup();
    selectWall();
    drag([10, 0], [
      [11, 1],
      [11.5, 2],
      [12, 3],
    ]);
    expect(screen.queryByTestId("snap-indicator")).toBeNull();
    expect(store.getState().history.past.map((entry) => entry.name)).toEqual(["move_vertex"]);
    expect(store.getState().queue.ops.at(-1)).toMatchObject({ operation: "move_vertex" });
    expect(wallCoords()[1]?.[0]).toBeCloseTo(12, 1);
    expect(wallCoords()[1]?.[1]).toBeCloseTo(3, 1);
  });

  it("endpoint snap beats orthogonal and records snap_endpoint", () => {
    const { store, drag, selectWall, wallCoords } = setup();
    selectWall();
    drag([10, 0], [[20.05, 5.03]]);
    expect(wallCoords()[1]).toEqual([20, 5]);
    expect(store.getState().history.past.at(-1)?.name).toBe("snap_endpoint");
  });

  it("grid snap records snap_grid with the configured grid size", () => {
    const { store, snap, drag, selectWall, wallCoords } = setup();
    snap.getState().setEnabled("grid", true);
    snap.getState().setGridM(1);
    selectWall();
    drag([10, 0], [[12.3, 3.2]]);
    expect(wallCoords()[1]).toEqual([12, 3]);
    expect(store.getState().history.past.at(-1)?.name).toBe("snap_grid");
  });

  it("Esc cancels the drag: nothing is committed", () => {
    const { store, svg, pointer, selectWall, wallCoords } = setup();
    selectWall();
    pointer("down", svg, [10, 0]);
    pointer("move", svg, [12, 3]);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByTestId("vertex-drag-preview")).toBeNull();
    pointer("up", svg, [12, 3]);
    expect(wallCoords()).toEqual([
      [0, 0],
      [10, 0],
    ]);
    expect(store.getState().history.past).toEqual([]);
  });

  it("arrow keys on a focused handle nudge the vertex by the grid step, Shift by a fine step", () => {
    const { store, selectWall, wallCoords } = setup();
    selectWall();
    const handle = screen.getByRole("button", { name: VERTEX_TEXT.handle(1, 2) });
    handle.focus();
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(wallCoords()[1]).toEqual([10, 0.1]);
    fireEvent.keyDown(handle, { key: "ArrowRight", shiftKey: true });
    expect(wallCoords()[1]?.[0]).toBeCloseTo(10.01, 9);
    expect(store.getState().history.past.map((entry) => entry.name)).toEqual([
      "move_vertex",
      "move_vertex",
    ]);
  });
});

describe("snapping while drawing", () => {
  it("a new line is created with the snapped end, recorded only as create", () => {
    const { store, svg, pointer } = setup("line");
    pointer("down", svg, [2, 4]);
    pointer("up", svg, [2, 4]);
    pointer("down", svg, [8, 4.03]);
    pointer("up", svg, [8, 4.03]);
    const id = store.getState().entities.order.find((key) => key.startsWith("local-"));
    const geometry = store.getState().entities.byId[id ?? ""]?.geometry as { coords_m: Point2[] };
    const [start, end] = geometry.coords_m;
    expect(end?.[1]).toBe(start?.[1]);
    expect(store.getState().history.past.map((entry) => entry.name)).toEqual(["add"]);
    expect(store.getState().queue.ops.map((op) => op.kind)).toEqual(["create"]);
  });
});
