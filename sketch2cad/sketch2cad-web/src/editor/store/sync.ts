// Minimal sync executor (jotdown H2): drains the editor store's operation queue through the typed
// API client, one operation at a time and in order: `beginSync` takes the head, `ackSync`
// removes it once the server answered, `failSync` puts it back on any error and the flush stops.
//
// It knows nothing about the edit lease: the caller must hold it (POST /plans/{id}/lease) before
// flushing. A 409 (lease missing, frozen revision) is reported as `conflict` so that the caller
// can re-take the lease or tell the user; other errors are reported as `failed`. Nothing retries.
import { ApiError, type ApiClient } from "../../api/client";
import { toApiRequest, type EditorStore } from "./entities";
import type { components } from "../../api/schema";
import type { QueuedOperation } from "./types";
import type { StoreApi } from "zustand/vanilla";

export interface SyncContext {
  revisionId: string;
}

export type FlushResult =
  | { status: "drained"; sent: number }
  /** An operation was already in flight (another flush is running). */
  | { status: "busy"; sent: number }
  | { status: "conflict"; sent: number; op: QueuedOperation; error: ApiError }
  | { status: "failed"; sent: number; op: QueuedOperation; error: unknown };

type SyncApi = Pick<ApiClient, "post" | "patch" | "delete">;

/** Send one queued operation; returns the server id of a created entity. */
async function send(
  api: SyncApi,
  op: QueuedOperation,
  store: StoreApi<EditorStore>,
  context: SyncContext,
): Promise<string | undefined> {
  const request = toApiRequest(op, {
    revisionId: context.revisionId,
    serverIds: store.getState().serverIds,
  });
  switch (request.method) {
    case "post": {
      const created = await api.post(request.path, request.options);
      return created.entities[0]?.id;
    }
    case "patch":
      // The generated type lists server-defaulted fields (confirm_constraint_removal) as required;
      // the API treats an omitted field as its default, so the body is sent as built.
      await api.patch(request.path, {
        ...request.options,
        body: request.options.body as components["schemas"]["EntityPatch"],
      });
      return undefined;
    case "delete":
      await api.delete(request.path, request.options);
      return undefined;
  }
}

/** Send the queued operations in order until the queue is empty or one fails. */
export async function flushQueue(
  api: SyncApi,
  store: StoreApi<EditorStore>,
  context: SyncContext,
): Promise<FlushResult> {
  let sent = 0;
  for (;;) {
    const op = store.getState().beginSync();
    if (op === undefined) {
      return store.getState().queue.ops.length === 0
        ? { status: "drained", sent }
        : { status: "busy", sent };
    }
    try {
      const serverId = await send(api, op, store, context);
      store.getState().ackSync(op.seq, serverId);
      sent += 1;
    } catch (error) {
      store.getState().failSync(op.seq);
      if (error instanceof ApiError && error.status === 409) {
        return { status: "conflict", sent, op, error };
      }
      return { status: "failed", sent, op, error };
    }
  }
}
