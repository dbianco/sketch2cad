// Editor store (jotdown H2): Zustand vanilla store, free of React, with normalized entities.
// Every user operation runs through Immer `produceWithPatches`, is recorded in the history
// (history.ts) and enqueues the API operation that mirrors it. React code reads it with
// `useStore(editorStore, selector)` from "zustand".
import { enablePatches, produceWithPatches, type Draft } from "immer";
import { createStore, type StoreApi } from "zustand/vanilla";
import {
  EMPTY_HISTORY,
  EMPTY_QUEUE,
  commitEntry,
  pruneSelection,
  redoState,
  releaseQueued,
  removeQueued,
  takeHead,
  undoState,
} from "./history";
import type {
  ApiOperation,
  EditorEntity,
  EditorState,
  EntitiesDoc,
  EntityAttributes,
  EntityId,
  EntityIn,
  EntityOperation,
  EntityOut,
  EntityPatchPayload,
  Geometry,
  Point2,
  QueuedOperation,
  SnapOperation,
  UserOperationName,
} from "./types";

enablePatches();

/** Prefix of the store ids of entities drawn locally and not yet created on the server. */
export const LOCAL_ID_PREFIX = "local-";

/** `reason` of the DELETE that reverts an entity creation on undo. */
export const UNDO_DELETE_REASON = "undo";

/** Fields of a relabel: new text/symbol geometry, new attributes, or both. */
export interface RelabelChange {
  geometry?: Geometry;
  attributes?: EntityAttributes;
}

export interface EditorActions {
  /** Replace the document with a revision's entities; clears history, queue and selection. */
  load(revisionId: string, entities: readonly EntityOut[]): void;
  /** Add an entity drawn by hand; returns its local id. */
  addEntity(input: EntityIn): EntityId;
  /** Move vertex `index`: `coords_m[index]`, the anchor (0) or a dimension's p1 (0) / p2 (1). */
  moveVertex(id: EntityId, index: number, point: Point2): void;
  /** Replace the geometry (same `kind`) as the result of a snap. */
  setGeometry(id: EntityId, geometry: Geometry, operation: SnapOperation): void;
  relabel(id: EntityId, change: RelabelChange): void;
  reclassify(id: EntityId, layer: string): void;
  /** Soft delete; undo restores the entity. */
  deleteEntity(id: EntityId, reason?: string): void;
  undo(): void;
  redo(): void;
  select(ids: readonly EntityId[]): void;
  clearSelection(): void;
  /** Hand the head of the queue to the sync; `undefined` if empty or one is already in flight. */
  beginSync(): QueuedOperation | undefined;
  /** The operation `seq` reached the server; `serverId` is the created entity's id. */
  ackSync(seq: number, serverId?: string): void;
  /** The operation `seq` failed; it goes back to `pending` at the head of the queue. */
  failSync(seq: number): void;
}

export type EditorStore = EditorState & EditorActions;

export function initialEditorState(): EditorState {
  return {
    revisionId: null,
    entities: { byId: {}, order: [] },
    selection: [],
    history: EMPTY_HISTORY,
    queue: EMPTY_QUEUE,
    serverIds: {},
    nextLocalId: 1,
  };
}

function toAttributes(raw: Record<string, unknown>): EntityAttributes {
  const attributes: EntityAttributes = {};
  for (const [key, value] of Object.entries(raw)) {
    if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
      attributes[key] = value as EntityAttributes[string];
    }
  }
  return attributes;
}

export function toEditorEntity(entity: EntityOut): EditorEntity {
  const { id, type, layer, geometry, attributes, ...remote } = entity;
  return {
    id,
    type,
    layer,
    geometry: structuredClone(geometry),
    attributes: toAttributes(attributes),
    deleted: remote.deleted_at !== null,
    remote,
  };
}

function toEntityIn(entity: EditorEntity): EntityIn {
  return {
    type: entity.type,
    layer: entity.layer,
    geometry: entity.geometry,
    attributes: entity.attributes,
  };
}

function setVertex(geometry: Draft<Geometry>, index: number, point: Point2): void {
  const value: Point2 = [point[0], point[1]];
  const fail = () => {
    throw new RangeError(`Vertex ${index} does not exist in a ${geometry.kind} geometry`);
  };
  if (!Number.isInteger(index) || index < 0) {
    fail();
  }
  switch (geometry.kind) {
    case "line":
    case "polyline":
    case "point":
      if (index >= geometry.coords_m.length) fail();
      geometry.coords_m[index] = value;
      return;
    case "text":
    case "symbol":
      if (index !== 0) fail();
      geometry.anchor_m = value;
      return;
    case "dimension":
      if (index === 0) geometry.p1_m = value;
      else if (index === 1) geometry.p2_m = value;
      else fail();
      return;
  }
}

function sameKind(entity: EditorEntity, geometry: Geometry): Geometry {
  if (geometry.kind !== entity.geometry.kind) {
    throw new TypeError(
      `Entity ${entity.id} has a ${entity.geometry.kind} geometry, not ${geometry.kind}`,
    );
  }
  return structuredClone(geometry);
}

function liveEntity(state: EditorState, id: EntityId): EditorEntity {
  const entity = state.entities.byId[id];
  if (entity === undefined || entity.deleted) {
    throw new Error(`Entity ${id} does not exist or is deleted`);
  }
  return entity;
}

type PatchField = keyof EntityPatchPayload & ("geometry" | "layer" | "attributes");

function patchOperation(
  entity: EditorEntity,
  operation: EntityOperation,
  fields: readonly PatchField[],
): ApiOperation {
  const payload: EntityPatchPayload = {};
  for (const field of fields) {
    if (field === "geometry") payload.geometry = entity.geometry;
    else if (field === "layer") payload.layer = entity.layer;
    else payload.attributes = entity.attributes;
  }
  return { kind: "patch", entityId: entity.id, operation, payload };
}

export function createEditorStore(): StoreApi<EditorStore> {
  return createStore<EditorStore>()((set, get) => {
    /** Apply `recipe` to the document and record it; a recipe that changes nothing is ignored. */
    function record(
      name: UserOperationName,
      entityId: EntityId,
      recipe: (draft: Draft<EntitiesDoc>) => void,
      ops: (before: EntitiesDoc, after: EntitiesDoc) => [ApiOperation, ApiOperation],
    ): void {
      const state = get();
      const [entities, patches, inversePatches] = produceWithPatches(state.entities, recipe);
      if (patches.length === 0) {
        return;
      }
      const [forward, inverse] = ops(state.entities, entities);
      set(commitEntry(state, entities, { name, entityId, patches, inversePatches, forward, inverse }));
    }

    function edit(
      id: EntityId,
      operation: EntityOperation,
      fields: readonly PatchField[],
      mutate: (entity: Draft<EditorEntity>, current: EditorEntity) => void,
    ): void {
      const current = liveEntity(get(), id);
      record(
        operation,
        id,
        (draft) => mutate(draft.byId[id] as Draft<EditorEntity>, current),
        (before, after) => [
          patchOperation(after.byId[id] as EditorEntity, operation, fields),
          patchOperation(before.byId[id] as EditorEntity, operation, fields),
        ],
      );
    }

    return {
      ...initialEditorState(),

      load(revisionId, entities) {
        const byId: Record<EntityId, EditorEntity> = {};
        for (const entity of entities) {
          byId[entity.id] = toEditorEntity(entity);
        }
        set({
          ...initialEditorState(),
          revisionId,
          entities: { byId, order: entities.map((entity) => entity.id) },
        });
      },

      addEntity(input) {
        const state = get();
        const id = `${LOCAL_ID_PREFIX}${state.nextLocalId}`;
        set({ nextLocalId: state.nextLocalId + 1 });
        const entity: EditorEntity = {
          id,
          type: input.type,
          layer: input.layer,
          geometry: structuredClone(input.geometry),
          attributes: structuredClone(input.attributes ?? {}),
          deleted: false,
          remote: null,
        };
        record(
          "add",
          id,
          (draft) => {
            draft.byId[id] = entity;
            draft.order.push(id);
          },
          () => [
            { kind: "create", entityId: id, operation: null, payload: toEntityIn(entity) },
            { kind: "delete", entityId: id, operation: null, payload: { reason: UNDO_DELETE_REASON } },
          ],
        );
        return id;
      },

      moveVertex(id, index, point) {
        edit(id, "move_vertex", ["geometry"], (entity) => setVertex(entity.geometry, index, point));
      },

      setGeometry(id, geometry, operation) {
        edit(id, operation, ["geometry"], (entity, current) => {
          entity.geometry = sameKind(current, geometry);
        });
      },

      relabel(id, change) {
        const fields: PatchField[] = [];
        if (change.geometry !== undefined) fields.push("geometry");
        if (change.attributes !== undefined) fields.push("attributes");
        if (fields.length === 0) {
          throw new Error("A relabel needs a geometry or attributes");
        }
        edit(id, "relabel", fields, (entity, current) => {
          if (change.geometry !== undefined) entity.geometry = sameKind(current, change.geometry);
          if (change.attributes !== undefined) entity.attributes = structuredClone(change.attributes);
        });
      },

      reclassify(id, layer) {
        edit(id, "reclassify", ["layer"], (entity) => {
          entity.layer = layer;
        });
      },

      deleteEntity(id, reason) {
        const current = liveEntity(get(), id);
        record(
          "delete",
          id,
          (draft) => {
            (draft.byId[id] as Draft<EditorEntity>).deleted = true;
          },
          () => [
            { kind: "delete", entityId: id, operation: null, payload: { reason: reason ?? null } },
            // The API has no undelete: reverting a sent delete creates the entity again.
            { kind: "create", entityId: id, operation: null, payload: toEntityIn(current) },
          ],
        );
      },

      undo() {
        set(undoState(get()));
      },

      redo() {
        set(redoState(get()));
      },

      select(ids) {
        set({ selection: pruneSelection([...new Set(ids)], get().entities) });
      },

      clearSelection() {
        set({ selection: [] });
      },

      beginSync() {
        const [queue, head] = takeHead(get().queue);
        if (head !== undefined) set({ queue });
        return head;
      },

      ackSync(seq, serverId) {
        const state = get();
        const op = state.queue.ops.find((queued) => queued.seq === seq);
        if (op === undefined) return;
        const serverIds =
          op.kind === "create" && serverId !== undefined
            ? { ...state.serverIds, [op.entityId]: serverId }
            : state.serverIds;
        set({ queue: removeQueued(state.queue, seq), serverIds });
      },

      failSync(seq) {
        set({ queue: releaseQueued(get().queue, seq) });
      },
    };
  });
}

/** The editor's store instance. */
export const editorStore = createEditorStore();

// Selectors. They return stable references for an unchanged state (Zustand v5 requirement).

export function selectEntity(state: EditorState, id: EntityId): EditorEntity | undefined {
  return state.entities.byId[id];
}

const visibleIdsCache = new WeakMap<EntitiesDoc, EntityId[]>();

/** Ids of the non-deleted entities in drawing order; memoized per document. */
export function selectVisibleIds(state: EditorState): EntityId[] {
  let ids = visibleIdsCache.get(state.entities);
  if (ids === undefined) {
    const { byId, order } = state.entities;
    ids = order.filter((id) => byId[id]?.deleted === false);
    visibleIdsCache.set(state.entities, ids);
  }
  return ids;
}

export const selectCanUndo = (state: EditorState): boolean => state.history.past.length > 0;
export const selectCanRedo = (state: EditorState): boolean => state.history.future.length > 0;
export const selectQueuedOperations = (state: EditorState): QueuedOperation[] => state.queue.ops;

// Mapping of a queued operation to an HTTP request, for the sync (task 060+).

export type ApiRequestDescriptor =
  | {
      method: "post";
      path: "/api/v1/revisions/{revision_id}/entities";
      options: { path: { revision_id: string }; body: { entities: EntityIn[] } };
    }
  | {
      method: "patch";
      path: "/api/v1/entities/{entity_id}";
      options: {
        path: { entity_id: string };
        body: EntityPatchPayload & { operation: EntityOperation };
      };
    }
  | {
      method: "delete";
      path: "/api/v1/entities/{entity_id}";
      options: { path: { entity_id: string }; query: { reason?: string | null } };
    };

export interface RequestContext {
  revisionId: string;
  serverIds: Record<EntityId, string>;
}

/** Server id of a store id; a local id must have been created (acked) first. */
export function resolveServerId(id: EntityId, serverIds: Record<EntityId, string>): string {
  const serverId = serverIds[id];
  if (serverId !== undefined) return serverId;
  if (id.startsWith(LOCAL_ID_PREFIX)) {
    throw new Error(`Entity ${id} has not been created on the server yet`);
  }
  return id;
}

/** Pure mapping of an API operation to the typed-client call that performs it. */
export function toApiRequest(op: ApiOperation, context: RequestContext): ApiRequestDescriptor {
  switch (op.kind) {
    case "create":
      return {
        method: "post",
        path: "/api/v1/revisions/{revision_id}/entities",
        options: { path: { revision_id: context.revisionId }, body: { entities: [op.payload] } },
      };
    case "patch":
      return {
        method: "patch",
        path: "/api/v1/entities/{entity_id}",
        options: {
          path: { entity_id: resolveServerId(op.entityId, context.serverIds) },
          body: { ...op.payload, operation: op.operation },
        },
      };
    case "delete":
      return {
        method: "delete",
        path: "/api/v1/entities/{entity_id}",
        options: {
          path: { entity_id: resolveServerId(op.entityId, context.serverIds) },
          query: op.payload.reason === null ? {} : { reason: op.payload.reason },
        },
      };
  }
}
