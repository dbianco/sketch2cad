import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient } from "../../api/client";
import { createEditorStore } from "./entities";
import { flushQueue } from "./sync";
import type { EntityOut } from "./types";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const SERVER_ID = "00000000-0000-4000-8000-0000000000e1";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function entityOut(id: string): EntityOut {
  return {
    id,
    type: "point",
    layer: "L_EVIDENCE",
    geometry: { kind: "point", coords_m: [[1, 1]] },
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

function setup() {
  const fetchMock = vi.fn<typeof fetch>();
  const api = createApiClient({ fetch: fetchMock });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, []);
  return { fetchMock, api, store };
}

function calls(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) {
  return fetchMock.mock.calls.map(([url, init]) => ({
    method: init?.method,
    url: String(url),
    body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
  }));
}

describe("flushQueue", () => {
  it("sends create, patch and delete in order, mapping the local id to the server id", async () => {
    const { fetchMock, api, store } = setup();
    const id = store.getState().addEntity({
      type: "point",
      layer: "L_EVIDENCE",
      geometry: { kind: "point", coords_m: [[1, 1]] },
    });
    store.getState().moveVertex(id, 0, [2, 2]);
    store.getState().deleteEntity(id, "duplicate");
    fetchMock
      .mockResolvedValueOnce(json({ entities: [entityOut(SERVER_ID)] }, 201))
      .mockResolvedValueOnce(json(entityOut(SERVER_ID)))
      .mockResolvedValueOnce(json(entityOut(SERVER_ID)));

    const result = await flushQueue(api, store, { revisionId: REVISION_ID });

    expect(result).toEqual({ status: "drained", sent: 3 });
    expect(store.getState().queue.ops).toEqual([]);
    expect(store.getState().serverIds).toEqual({ [id]: SERVER_ID });
    expect(calls(fetchMock)).toEqual([
      {
        method: "POST",
        url: `/api/v1/revisions/${REVISION_ID}/entities`,
        body: { entities: [expect.objectContaining({ type: "point" })] },
      },
      {
        method: "PATCH",
        url: `/api/v1/entities/${SERVER_ID}`,
        body: { geometry: { kind: "point", coords_m: [[2, 2]] }, operation: "move_vertex" },
      },
      {
        method: "DELETE",
        url: `/api/v1/entities/${SERVER_ID}?reason=duplicate`,
        body: undefined,
      },
    ]);
  });

  it("stops on a 409 and reports it; the operation stays queued for a retry", async () => {
    const { fetchMock, api, store } = setup();
    store.getState().load(REVISION_ID, [entityOut(SERVER_ID)]);
    store.getState().moveVertex(SERVER_ID, 0, [3, 3]);
    store.getState().moveVertex(SERVER_ID, 0, [4, 4]);
    fetchMock.mockResolvedValueOnce(
      json({ title: "Conflict", status: 409, detail: "lease_required" }, 409),
    );

    const result = await flushQueue(api, store, { revisionId: REVISION_ID });

    expect(result.status).toBe("conflict");
    if (result.status !== "conflict") throw new Error("unreachable");
    expect(result.sent).toBe(0);
    expect(result.error).toBeInstanceOf(ApiError);
    expect(result.op.operation).toBe("move_vertex");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(store.getState().queue.ops.map((op) => op.status)).toEqual(["pending", "pending"]);
  });

  it("reports other errors as failed, and an in-flight head as busy", async () => {
    const { fetchMock, api, store } = setup();
    store.getState().load(REVISION_ID, [entityOut(SERVER_ID)]);
    store.getState().moveVertex(SERVER_ID, 0, [3, 3]);
    fetchMock.mockRejectedValueOnce(new TypeError("network down"));
    const failed = await flushQueue(api, store, { revisionId: REVISION_ID });
    expect(failed).toMatchObject({ status: "failed", sent: 0 });
    expect(store.getState().queue.ops[0]?.status).toBe("pending");

    store.getState().beginSync();
    expect(await flushQueue(api, store, { revisionId: REVISION_ID })).toEqual({
      status: "busy",
      sent: 0,
    });
  });
});
