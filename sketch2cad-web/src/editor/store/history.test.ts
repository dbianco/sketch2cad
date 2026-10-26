import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  LOCAL_ID_PREFIX,
  UNDO_DELETE_REASON,
  createEditorStore,
  selectCanRedo,
  selectCanUndo,
  selectVisibleIds,
  toApiRequest,
  type EditorStore,
} from "./entities";
import { HISTORY_LIMIT } from "./history";
import type { EntityIn, EntityOut, Geometry, Point2 } from "./types";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const LAYERS = ["WALLS", "ROAD", "EVIDENCE", "TEXT", "DIMENSIONS"] as const;

function entityOut(id: string, geometry: Geometry, layer = "WALLS"): EntityOut {
  const type = geometry.kind;
  return {
    id,
    type,
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
    updated_at: "2026-10-25T00:00:00Z",
  };
}

function polyline(coords: Point2[]): Geometry {
  return { kind: "polyline", closed: false, coords_m: coords };
}

function loadedStore(entities: EntityOut[]) {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, entities);
  return store;
}

describe("undo and redo of a vertex move (H2-10)", () => {
  it("restores the exact coordinate array and redo reapplies the move", () => {
    const original: Point2[] = [
      [0, 0],
      [1.234567, 2.5],
      [3.1, -0.000001],
    ];
    const store = loadedStore([entityOut("e1", polyline(original))]);
    const coords = () => {
      const geometry = store.getState().entities.byId["e1"]?.geometry;
      return geometry?.kind === "polyline" ? geometry.coords_m : undefined;
    };

    store.getState().moveVertex("e1", 1, [9.87654321, -4.2]);
    const moved = [
      [0, 0],
      [9.87654321, -4.2],
      [3.1, -0.000001],
    ];
    expect(coords()).toEqual(moved);

    store.getState().undo();
    expect(coords()).toEqual(original);

    store.getState().redo();
    expect(coords()).toEqual(moved);
  });

  it("enqueues a move_vertex PATCH with the whole new geometry", () => {
    const store = loadedStore([entityOut("e1", polyline([[0, 0], [1, 1]]))]);
    store.getState().moveVertex("e1", 0, [5, 5]);

    const [op] = store.getState().queue.ops;
    expect(op).toMatchObject({ kind: "patch", entityId: "e1", operation: "move_vertex" });
    expect(op && toApiRequest(op, { revisionId: REVISION_ID, serverIds: {} })).toEqual({
      method: "patch",
      path: "/api/v1/entities/{entity_id}",
      options: {
        path: { entity_id: "e1" },
        body: {
          operation: "move_vertex",
          geometry: { kind: "polyline", closed: false, coords_m: [[5, 5], [1, 1]] },
        },
      },
    });
  });

  it("rejects a vertex index outside the geometry", () => {
    const store = loadedStore([entityOut("e1", polyline([[0, 0], [1, 1]]))]);
    expect(() => store.getState().moveVertex("e1", 2, [0, 0])).toThrow(RangeError);
    expect(store.getState().history.past).toHaveLength(0);
  });
});

describe("history", () => {
  it("clears the redo stack on a new operation", () => {
    const store = loadedStore([entityOut("e1", polyline([[0, 0], [1, 1]]))]);
    store.getState().moveVertex("e1", 0, [2, 2]);
    store.getState().undo();
    expect(selectCanRedo(store.getState())).toBe(true);

    store.getState().reclassify("e1", "ROAD");
    expect(selectCanRedo(store.getState())).toBe(false);
    store.getState().redo();
    expect(store.getState().entities.byId["e1"]?.layer).toBe("ROAD");
  });

  it(`keeps at most ${HISTORY_LIMIT} undoable operations`, () => {
    const store = loadedStore([entityOut("e1", polyline([[0, 0], [1, 1]]))]);
    for (let step = 1; step <= HISTORY_LIMIT + 5; step += 1) {
      store.getState().moveVertex("e1", 0, [step, step]);
    }
    expect(store.getState().history.past).toHaveLength(HISTORY_LIMIT);

    for (let step = 0; step < HISTORY_LIMIT; step += 1) {
      store.getState().undo();
    }
    expect(selectCanUndo(store.getState())).toBe(false);
    const geometry = store.getState().entities.byId["e1"]?.geometry;
    // The five oldest moves fell out of the history and stay applied.
    expect(geometry?.kind === "polyline" && geometry.coords_m[0]).toEqual([5, 5]);
  });

  it("restores a deleted entity on undo", () => {
    const store = loadedStore([entityOut("e1", polyline([[0, 0], [1, 1]]))]);
    store.getState().select(["e1"]);
    store.getState().deleteEntity("e1", "duplicate");
    expect(store.getState().entities.byId["e1"]?.deleted).toBe(true);
    expect(selectVisibleIds(store.getState())).toEqual([]);
    expect(store.getState().selection).toEqual([]);

    store.getState().undo();
    expect(store.getState().entities.byId["e1"]?.deleted).toBe(false);
    expect(selectVisibleIds(store.getState())).toEqual(["e1"]);
  });
});

describe("operation queue", () => {
  const input: EntityIn = {
    type: "line",
    layer: "WALLS",
    geometry: { kind: "line", coords_m: [[0, 0], [2, 0]] },
  };

  it("queues each operation with its API operation name", () => {
    const store = loadedStore([
      entityOut("e1", polyline([[0, 0], [1, 1]])),
      entityOut("t1", { kind: "text", anchor_m: [0, 0], content: "A", height_mm_paper: 3, rotation_deg: 0 }),
    ]);
    const s = () => store.getState();
    const added = s().addEntity(input);
    s().moveVertex("e1", 1, [3, 3]);
    s().setGeometry("e1", polyline([[0, 0], [3, 0]]), "snap_orthogonal");
    s().setGeometry("e1", polyline([[0, 0], [3, 1]]), "snap_endpoint");
    s().setGeometry("e1", polyline([[0, 0], [3, 2]]), "snap_grid");
    s().relabel("t1", { geometry: { kind: "text", anchor_m: [0, 0], content: "B", height_mm_paper: 3, rotation_deg: 0 } });
    s().reclassify("e1", "ROAD");
    s().deleteEntity("e1", "wrong");

    expect(added.startsWith(LOCAL_ID_PREFIX)).toBe(true);
    expect(s().queue.ops.map((op) => [op.kind, op.operation, op.entityId])).toEqual([
      ["create", null, added],
      ["patch", "move_vertex", "e1"],
      ["patch", "snap_orthogonal", "e1"],
      ["patch", "snap_endpoint", "e1"],
      ["patch", "snap_grid", "e1"],
      ["patch", "relabel", "t1"],
      ["patch", "reclassify", "e1"],
      ["delete", null, "e1"],
    ]);
    expect(s().queue.ops.map((op) => op.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    const reclassify = s().queue.ops[6];
    expect(reclassify?.payload).toEqual({ layer: "ROAD" });
    const remove = s().queue.ops[7];
    expect(remove && toApiRequest(remove, { revisionId: REVISION_ID, serverIds: {} })).toEqual({
      method: "delete",
      path: "/api/v1/entities/{entity_id}",
      options: { path: { entity_id: "e1" }, query: { reason: "wrong" } },
    });
  });

  it("drops a pending operation on undo and queues it again on redo", () => {
    const store = loadedStore([]);
    const id = store.getState().addEntity(input);
    store.getState().undo();
    expect(store.getState().queue.ops).toEqual([]);
    expect(store.getState().entities).toEqual({ byId: {}, order: [] });

    store.getState().redo();
    expect(store.getState().queue.ops).toMatchObject([{ kind: "create", entityId: id }]);
  });

  it("queues the inverse operation when undoing an operation already sent", () => {
    const store = loadedStore([]);
    const id = store.getState().addEntity(input);
    const head = store.getState().beginSync();
    expect(head?.kind).toBe("create");
    expect(head && toApiRequest(head, { revisionId: REVISION_ID, serverIds: {} })).toEqual({
      method: "post",
      path: "/api/v1/revisions/{revision_id}/entities",
      options: { path: { revision_id: REVISION_ID }, body: { entities: [{ ...input, attributes: {} }] } },
    });
    expect(store.getState().beginSync()).toBeUndefined();
    store.getState().ackSync(head?.seq ?? 0, "server-1");
    expect(store.getState().serverIds).toEqual({ [id]: "server-1" });

    store.getState().undo();
    const [inverse] = store.getState().queue.ops;
    expect(inverse).toMatchObject({ kind: "delete", entityId: id, payload: { reason: UNDO_DELETE_REASON } });
    expect(inverse && toApiRequest(inverse, { revisionId: REVISION_ID, serverIds: store.getState().serverIds }))
      .toMatchObject({ options: { path: { entity_id: "server-1" } } });

    // Redo before the inverse is sent just drops it.
    store.getState().redo();
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("treats an in-flight operation as sent and puts a failed one back", () => {
    const store = loadedStore([entityOut("e1", polyline([[0, 0], [1, 1]]))]);
    store.getState().reclassify("e1", "ROAD");
    const head = store.getState().beginSync();
    store.getState().undo();
    expect(store.getState().queue.ops.map((op) => [op.status, op.payload])).toEqual([
      ["in_flight", { layer: "ROAD" }],
      ["pending", { layer: "WALLS" }],
    ]);
    store.getState().failSync(head?.seq ?? 0);
    expect(store.getState().queue.ops[0]?.status).toBe("pending");
  });

  it("refuses to address a local entity that was not created yet", () => {
    const store = loadedStore([]);
    const id = store.getState().addEntity(input);
    store.getState().reclassify(id, "ROAD");
    const patch = store.getState().queue.ops[1];
    expect(() => patch && toApiRequest(patch, { revisionId: REVISION_ID, serverIds: {} })).toThrow(
      /not been created/,
    );
  });
});

// Property test: random operations over a random document, all undone, then all redone.

const coordArb = fc.double({ min: -1000, max: 1000, noNaN: true, noDefaultInfinity: true });
const pointArb: fc.Arbitrary<Point2> = fc.tuple(coordArb, coordArb);

const geometryArb: fc.Arbitrary<Geometry> = fc.oneof(
  fc.array(pointArb, { minLength: 2, maxLength: 12 }).map((coords_m): Geometry => ({ kind: "line", coords_m })),
  fc
    .record({ coords: fc.array(pointArb, { minLength: 3, maxLength: 12 }), closed: fc.boolean() })
    .map(({ coords, closed }): Geometry => ({ kind: "polyline", closed, coords_m: coords })),
  pointArb.map((p): Geometry => ({ kind: "point", coords_m: [p] })),
  pointArb.map((p): Geometry => ({ kind: "text", anchor_m: p, content: "N", height_mm_paper: 3, rotation_deg: 0 })),
  pointArb.map(
    (p): Geometry => ({ kind: "symbol", anchor_m: p, symbol_id: "evidence", rotation_deg: 0, scale: 1 }),
  ),
  fc
    .tuple(pointArb, pointArb)
    .map(([p1_m, p2_m]): Geometry => ({ kind: "dimension", p1_m, p2_m, offset_m: 0 })),
);

const initialArb = fc.array(fc.tuple(geometryArb, fc.constantFrom(...LAYERS)), {
  minLength: 1,
  maxLength: 15,
});

type OpSpec =
  | { op: "add"; geometry: Geometry }
  | { op: "move"; pick: number; vertex: number; point: Point2 }
  | { op: "snap"; pick: number; delta: Point2 }
  | { op: "relabel"; pick: number }
  | { op: "reclassify"; pick: number; layer: number }
  | { op: "delete"; pick: number };

const pickArb = fc.nat({ max: 1000 });
const opArb: fc.Arbitrary<OpSpec> = fc.oneof(
  geometryArb.map((geometry): OpSpec => ({ op: "add", geometry })),
  fc.record({ op: fc.constant("move" as const), pick: pickArb, vertex: fc.nat({ max: 20 }), point: pointArb }),
  fc.record({ op: fc.constant("snap" as const), pick: pickArb, delta: pointArb }),
  fc.record({ op: fc.constant("relabel" as const), pick: pickArb }),
  fc.record({ op: fc.constant("reclassify" as const), pick: pickArb, layer: fc.nat({ max: 3 }) }),
  fc.record({ op: fc.constant("delete" as const), pick: pickArb }),
);

function vertexCount(geometry: Geometry): number {
  switch (geometry.kind) {
    case "line":
    case "polyline":
    case "point":
      return geometry.coords_m.length;
    case "dimension":
      return 2;
    default:
      return 1;
  }
}

function shifted(geometry: Geometry, [dx, dy]: Point2): Geometry {
  const move = ([x, y]: Point2): Point2 => [x + dx, y + dy];
  switch (geometry.kind) {
    case "line":
    case "polyline":
    case "point":
      return { ...geometry, coords_m: geometry.coords_m.map(move) };
    case "text":
    case "symbol":
      return { ...geometry, anchor_m: move(geometry.anchor_m) };
    case "dimension":
      return { ...geometry, p1_m: move(geometry.p1_m), p2_m: move(geometry.p2_m) };
  }
}

/** Apply one generated operation; always changes the document (so it is always recorded). */
function applySpec(store: EditorStore, spec: OpSpec, step: number): void {
  const visible = selectVisibleIds(store);
  if (spec.op === "add" || visible.length === 0) {
    const geometry = spec.op === "add" ? spec.geometry : polyline([[0, 0], [1, 1]]);
    store.addEntity({ type: geometry.kind, layer: "WALLS", geometry });
    return;
  }
  const id = visible[spec.pick % visible.length] as string;
  const entity = store.entities.byId[id];
  if (entity === undefined) throw new Error("unreachable");
  switch (spec.op) {
    case "move":
      store.moveVertex(id, spec.vertex % vertexCount(entity.geometry), spec.point);
      return;
    case "snap":
      store.setGeometry(id, shifted(entity.geometry, spec.delta), "snap_grid");
      return;
    case "relabel":
      store.relabel(id, { attributes: { label: `L${step}` } });
      return;
    case "reclassify": {
      const others = LAYERS.filter((layer) => layer !== entity.layer);
      store.reclassify(id, others[spec.layer % others.length] as string);
      return;
    }
    case "delete":
      store.deleteEntity(id);
      return;
  }
}

describe("undo/redo property", () => {
  it("undoing 50 random operations restores the initial state and redoing them returns", () => {
    fc.assert(
      fc.property(initialArb, fc.array(opArb, { minLength: 50, maxLength: 50 }), (initial, specs) => {
        const store = loadedStore(
          initial.map(([geometry, layer], index) => entityOut(`e${index}`, geometry, layer)),
        );
        const start = store.getState().entities;
        const startCopy = structuredClone(start);

        specs.forEach((spec, step) => applySpec(store.getState(), spec, step));
        expect(store.getState().history.past).toHaveLength(50);
        const edited = structuredClone(store.getState().entities);

        for (let step = 0; step < 50; step += 1) store.getState().undo();
        expect(store.getState().entities).toEqual(startCopy);
        expect(store.getState().queue.ops).toEqual([]);
        expect(selectCanUndo(store.getState())).toBe(false);

        for (let step = 0; step < 50; step += 1) store.getState().redo();
        expect(store.getState().entities).toEqual(edited);
        expect(store.getState().queue.ops).toHaveLength(50);
        expect(start).toEqual(startCopy);
      }),
      { numRuns: 100 },
    );
  });
});
