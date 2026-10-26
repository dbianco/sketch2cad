// Types of the editor store (jotdown H2, spec section 8.2): normalized entities, selection,
// undo/redo history of Immer patches and the queue of API operations waiting to be synced.
import type { Patch } from "immer";
import type { components } from "../../api/schema";

type Schemas = components["schemas"];

export type EntityOut = Schemas["EntityOut"];
export type EntityIn = Schemas["EntityIn"];
export type EntityType = Schemas["EntityType"];
export type EntityOperation = Schemas["EntityOperation"];
export type Geometry = EntityOut["geometry"];
export type GeometryKind = Geometry["kind"];
export type Point2 = [number, number];

/** Snap operations: they replace the whole geometry (spec section 8.2, US-06). */
export type SnapOperation = Extract<EntityOperation, `snap_${string}`>;

/** Entity attributes as the API accepts them (`EntityIn.attributes`). */
export type EntityAttributes = NonNullable<EntityIn["attributes"]>;

/** Store key of an entity: the server id, or a client id (`local-<n>`) until it is created. */
export type EntityId = string;

/** Fields that only the server sets; `null` for an entity drawn locally and not yet synced. */
export type RemoteEntityFields = Omit<EntityOut, "id" | "type" | "layer" | "geometry" | "attributes">;

/** An entity in the store: the editable part of `EntityOut` plus a local soft-delete flag. */
export interface EditorEntity {
  id: EntityId;
  type: EntityType;
  layer: string;
  geometry: Geometry;
  attributes: EntityAttributes;
  /** Soft delete mirrored locally so that undo can restore the entity. */
  deleted: boolean;
  remote: RemoteEntityFields | null;
}

/** The undoable document: entities normalized by id plus their drawing order. */
export interface EntitiesDoc {
  byId: Record<EntityId, EditorEntity>;
  order: EntityId[];
}

/** Changed fields of a `PATCH /entities/{id}` (an `EntityPatch` without `operation`). */
export type EntityPatchPayload = Partial<Omit<Schemas["EntityPatch"], "operation">>;

/** One API call a user operation needs, before it is sent. */
export type ApiOperation =
  | { kind: "create"; entityId: EntityId; operation: null; payload: EntityIn }
  | { kind: "patch"; entityId: EntityId; operation: EntityOperation; payload: EntityPatchPayload }
  | { kind: "delete"; entityId: EntityId; operation: null; payload: { reason: string | null } };

/**
 * A queued API operation. `pending` operations can still be dropped by undo/redo;
 * `in_flight` ones were handed to the sync and count as sent.
 */
export type QueuedOperation = ApiOperation & {
  seq: number;
  status: "pending" | "in_flight";
};

/** Names of the user operations of the store, as recorded in the history. */
export type UserOperationName = "add" | "delete" | EntityOperation;

/** One undoable user operation. */
export interface HistoryEntry {
  name: UserOperationName;
  entityId: EntityId;
  patches: Patch[];
  inversePatches: Patch[];
  /** API operation that applies the change on the server. */
  forward: ApiOperation;
  /** API operation that reverts it on the server once `forward` was sent. */
  inverse: ApiOperation;
  /**
   * `seq` of the last operation this entry put in the queue (forward on commit/redo, inverse on
   * undo), or `null` when nothing of it is queued.
   */
  queuedSeq: number | null;
}

export interface HistoryState {
  /** Applied entries, oldest first; undo pops the last one. */
  past: HistoryEntry[];
  /** Undone entries, the next one to redo last. */
  future: HistoryEntry[];
}

export interface OperationQueue {
  /** FIFO: the sync sends `ops[0]` first. */
  ops: QueuedOperation[];
  nextSeq: number;
}

export interface EditorState {
  revisionId: string | null;
  entities: EntitiesDoc;
  /** UI state, not part of the history. */
  selection: EntityId[];
  history: HistoryState;
  queue: OperationQueue;
  /** Server id of each entity created through the queue, keyed by its store id. */
  serverIds: Record<EntityId, string>;
  nextLocalId: number;
}
