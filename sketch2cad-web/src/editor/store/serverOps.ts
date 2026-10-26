// Server-computed entity operations (jotdown H3: fix_length, triangulate, baseline_offset). The
// server computes the new geometry and recomputes the entities that depend on it, so the store's
// local history and queue cannot express them. `runServerOperation`:
//
// 1. sends the pending queue first (flushQueue), so that the server works on what the user sees,
//    and gives up with a message when it cannot be drained;
// 2. sends the PATCH directly through the typed client (which adds the CSRF header);
// 3. reloads the revision's entities into the store with `load`, because other entities may have
//    changed too. `load` clears the undo history: undo does not cross a server-side operation,
//    and the tools say so before the user applies one. Edits queued while the PATCH was in flight
//    are flushed before the reload so that none is lost;
// 4. turns every failure into a Spanish message.
//
// It does not take the edit lease or turn the editor read-only: the editor page does both.
import { ApiError, type ApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import {
  PLAN_MESSAGES,
  planErrorMessage,
  problemCode,
  problemHolder,
  v12Message,
} from "../../features/plans/messages";
import { DISTANCE_MESSAGE } from "../util/parseDistance";
import { resolveServerId, type EditorStore } from "./entities";
import { flushQueue, type FlushResult, type SyncContext } from "./sync";
import type { EntityId } from "./types";
import type { StoreApi } from "zustand/vanilla";

type Schemas = components["schemas"];

/** Body of a server-computed PATCH; the server defaults `confirm_constraint_removal`. */
export type ServerOperationBody = Partial<Schemas["EntityPatch"]> &
  Pick<Schemas["EntityPatch"], "operation">;

export type ServerOperationResult =
  | { ok: true; entity: Schemas["EntityPatchOut"] }
  | { ok: false; message: string; error?: unknown };

export type ServerOperationApi = Pick<ApiClient, "get" | "post" | "patch" | "delete">;

export const SERVER_OPERATION_TEXT = {
  busy: "Hay cambios sincronizándose. Inténtalo de nuevo en unos segundos.",
  flushFailed: "No se pudieron enviar los cambios pendientes; la operación no se aplicó.",
  invalid: "El servidor no aceptó estos datos. Revisa los valores e inténtalo de nuevo.",
  constraintRemoval: "La entidad tiene una restricción métrica que esta operación quitaría.",
  otherUser: "Otra persona",
  failed: "No se pudo aplicar la operación. Inténtalo de nuevo.",
  reloadFailed: "La operación se aplicó, pero no se pudo recargar el plano. Recarga la página.",
  noUndo: "La calcula el servidor: Deshacer no la revierte.",
} as const;

/** Spanish message of a failed server operation; `messages` adds 422 messages by `code`. */
export function serverOperationMessage(
  error: unknown,
  messages: Readonly<Record<string, string>> = {},
): string {
  const code = problemCode(error);
  if (error instanceof ApiError && error.status === 422) {
    if (code === "V-04") return DISTANCE_MESSAGE;
    return (code !== undefined ? messages[code] : undefined) ?? SERVER_OPERATION_TEXT.invalid;
  }
  if (error instanceof ApiError && error.status === 409) {
    if (code === "lease_required") return PLAN_MESSAGES.leaseLost;
    if (code === "constraint_removal_required") return SERVER_OPERATION_TEXT.constraintRemoval;
    if (code === "V-12" && problemHolder(error) === null) {
      return v12Message(SERVER_OPERATION_TEXT.otherUser);
    }
    return planErrorMessage(error, PLAN_MESSAGES.notEditable);
  }
  return planErrorMessage(error, SERVER_OPERATION_TEXT.failed);
}

type Failure = Extract<ServerOperationResult, { ok: false }>;

/** Message of a queue that could not be drained before a server-side change. */
export function flushFailure(result: Exclude<FlushResult, { status: "drained" }>): Failure {
  if (result.status === "busy") return { ok: false, message: SERVER_OPERATION_TEXT.busy };
  if (result.status === "conflict") {
    return { ok: false, message: serverOperationMessage(result.error), error: result.error };
  }
  return { ok: false, message: SERVER_OPERATION_TEXT.flushFailed, error: result.error };
}

/**
 * Apply a server-computed operation to `entityId` (a store id) of `context.revisionId` and load
 * the server's entities into `store`. A `body` function is called after the queue is flushed with
 * a resolver of store ids to server ids, for bodies that name other entities (references).
 */
export async function runServerOperation(
  api: ServerOperationApi,
  store: StoreApi<EditorStore>,
  context: SyncContext,
  entityId: EntityId,
  body: ServerOperationBody | ((serverId: (id: EntityId) => string) => ServerOperationBody),
  messages: Readonly<Record<string, string>> = {},
): Promise<ServerOperationResult> {
  const flushed = await flushQueue(api, store, context);
  if (flushed.status !== "drained") return flushFailure(flushed);

  let entity: Schemas["EntityPatchOut"];
  try {
    const serverId = (id: EntityId) => resolveServerId(id, store.getState().serverIds);
    const payload = typeof body === "function" ? body(serverId) : body;
    entity = await api.patch("/api/v1/entities/{entity_id}", {
      path: { entity_id: serverId(entityId) },
      // The generated type marks the server-defaulted confirm_constraint_removal as required.
      body: payload as Schemas["EntityPatch"],
    });
  } catch (error) {
    return { ok: false, message: serverOperationMessage(error, messages), error };
  }

  const reloaded = await reloadEntities(api, store, context);
  return reloaded.ok ? { ok: true, entity } : reloaded;
}

/**
 * Load the revision's entities into `store` after a server-side change. Two rounds: edits queued
 * while the change was in flight are sent, then the entities are read again.
 */
export async function reloadEntities(
  api: ServerOperationApi,
  store: StoreApi<EditorStore>,
  context: SyncContext,
): Promise<{ ok: true } | Failure> {
  for (let round = 0; round < 2; round += 1) {
    try {
      const { entities } = await api.get("/api/v1/revisions/{revision_id}/entities", {
        path: { revision_id: context.revisionId },
      });
      if (store.getState().queue.ops.length === 0) {
        store.getState().load(context.revisionId, entities);
        return { ok: true };
      }
    } catch (error) {
      return { ok: false, message: SERVER_OPERATION_TEXT.reloadFailed, error };
    }
    const again = await flushQueue(api, store, context);
    if (again.status !== "drained") return flushFailure(again);
  }
  return { ok: false, message: SERVER_OPERATION_TEXT.reloadFailed };
}
