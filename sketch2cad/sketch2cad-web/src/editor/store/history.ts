// Undo/redo over Immer patches and the operation queue they keep consistent (jotdown H2).
//
// Each user operation becomes a HistoryEntry with the patches of the entities document, their
// inverse, and the API operation that applies (`forward`) or reverts (`inverse`) it on the
// server. The queue is FIFO and owned by the sync (task 060+), which takes the head with
// `beginSync`, then removes it with `ackSync` or puts it back with `failSync`.
//
// Undo and redo keep the queue equal to "what the server still needs":
// * if the entry's last queued operation is still `pending`, nothing was sent, so it is dropped;
// * otherwise it was sent (or is in flight), so the opposite API operation is enqueued.
// Because the queue is FIFO and undo is LIFO, a still-pending operation of the entry being undone
// is only followed by operations of later entries, which were undone (dropped) first.
import { applyPatches, enablePatches } from "immer";
import type {
  ApiOperation,
  EditorState,
  EntitiesDoc,
  EntityId,
  HistoryEntry,
  HistoryState,
  OperationQueue,
  QueuedOperation,
} from "./types";

enablePatches();

/**
 * Maximum number of undoable operations. Older entries are dropped (their queued operations are
 * still synced). 200 keeps a long editing session undoable while bounding the memory used by
 * patches of up to 2000 entities (spec section 17).
 */
export const HISTORY_LIMIT = 200;

export const EMPTY_HISTORY: HistoryState = { past: [], future: [] };
export const EMPTY_QUEUE: OperationQueue = { ops: [], nextSeq: 1 };

/** Append an operation to the queue; returns the new queue and the operation's `seq`. */
export function enqueue(queue: OperationQueue, op: ApiOperation): [OperationQueue, number] {
  const seq = queue.nextSeq;
  const queued = { ...op, seq, status: "pending" } as QueuedOperation;
  return [{ ops: [...queue.ops, queued], nextSeq: seq + 1 }, seq];
}

/** Remove the operation `seq` from the queue, whatever its status. */
export function removeQueued(queue: OperationQueue, seq: number): OperationQueue {
  return { ...queue, ops: queue.ops.filter((op) => op.seq !== seq) };
}

/** Mark the head of the queue as in flight and return it; `undefined` if empty or busy. */
export function takeHead(queue: OperationQueue): [OperationQueue, QueuedOperation | undefined] {
  const head = queue.ops[0];
  if (head === undefined || head.status !== "pending") {
    return [queue, undefined];
  }
  const started: QueuedOperation = { ...head, status: "in_flight" };
  return [{ ...queue, ops: [started, ...queue.ops.slice(1)] }, started];
}

/** Put an in-flight operation back to `pending` so the sync can retry it. */
export function releaseQueued(queue: OperationQueue, seq: number): OperationQueue {
  return {
    ...queue,
    ops: queue.ops.map((op) => (op.seq === seq ? { ...op, status: "pending" } : op)),
  };
}

/** Selection without entities that no longer exist or are deleted. */
export function pruneSelection(selection: EntityId[], entities: EntitiesDoc): EntityId[] {
  const kept = selection.filter((id) => {
    const entity = entities.byId[id];
    return entity !== undefined && !entity.deleted;
  });
  return kept.length === selection.length ? selection : kept;
}

/**
 * Record a new user operation: `entities` is the document after it. Pushes the entry (bounded by
 * HISTORY_LIMIT), clears the redo stack and enqueues `entry.forward`.
 */
export function commitEntry(
  state: EditorState,
  entities: EntitiesDoc,
  entry: Omit<HistoryEntry, "queuedSeq">,
): EditorState {
  const [queue, seq] = enqueue(state.queue, entry.forward);
  const past = [...state.history.past, { ...entry, queuedSeq: seq }];
  if (past.length > HISTORY_LIMIT) {
    past.splice(0, past.length - HISTORY_LIMIT);
  }
  return {
    ...state,
    entities,
    selection: pruneSelection(state.selection, entities),
    history: { past, future: [] },
    queue,
  };
}

/** Drop the entry's pending operation, or enqueue `fallback` when it was already sent. */
function compensate(
  queue: OperationQueue,
  queuedSeq: number | null,
  fallback: ApiOperation,
): [OperationQueue, number | null] {
  const queued = queuedSeq === null ? undefined : queue.ops.find((op) => op.seq === queuedSeq);
  if (queued !== undefined && queued.status === "pending") {
    return [removeQueued(queue, queued.seq), null];
  }
  return enqueue(queue, fallback);
}

/** Undo the last operation: apply its inverse patches and fix the queue. */
export function undoState(state: EditorState): EditorState {
  const entry = state.history.past.at(-1);
  if (entry === undefined) {
    return state;
  }
  const entities = applyPatches(state.entities, entry.inversePatches);
  const [queue, queuedSeq] = compensate(state.queue, entry.queuedSeq, entry.inverse);
  return {
    ...state,
    entities,
    selection: pruneSelection(state.selection, entities),
    history: {
      past: state.history.past.slice(0, -1),
      future: [...state.history.future, { ...entry, queuedSeq }],
    },
    queue,
  };
}

/** Redo the last undone operation: apply its patches and fix the queue. */
export function redoState(state: EditorState): EditorState {
  const entry = state.history.future.at(-1);
  if (entry === undefined) {
    return state;
  }
  const entities = applyPatches(state.entities, entry.patches);
  const [queue, queuedSeq] = compensate(state.queue, entry.queuedSeq, entry.forward);
  return {
    ...state,
    entities,
    selection: pruneSelection(state.selection, entities),
    history: {
      past: [...state.history.past, { ...entry, queuedSeq }],
      future: state.history.future.slice(0, -1),
    },
    queue,
  };
}
