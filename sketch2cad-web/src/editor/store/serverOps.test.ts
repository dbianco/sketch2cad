import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient } from "../../api/client";
import { PLAN_MESSAGES, V10_MESSAGE, v12Message } from "../../features/plans/messages";
import { DISTANCE_MESSAGE } from "../util/parseDistance";
import { createEditorStore } from "./entities";
import { SERVER_OPERATION_TEXT, runServerOperation, serverOperationMessage } from "./serverOps";
import type { EntityOut, Point2 } from "./types";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const LINE_ID = "00000000-0000-4000-8000-0000000000e1";
const CREATED_ID = "00000000-0000-4000-8000-0000000000e2";
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const BODY = { operation: "fix_length", anchor_vertex: 0, free_vertex: 1, value_m: 4.2 } as const;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function lineOut(coords: Point2[], id = LINE_ID): EntityOut {
  return {
    id,
    type: "line",
    layer: "L_STRUCTURE",
    geometry: { kind: "line", coords_m: coords },
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

const STRETCHED = lineOut([[0, 0], [4.2, 0]]);

type Handler = (method: string, url: string) => Response | undefined;

/** Answers the sync POST, the PATCH and the reload GET; `override` replaces any of them. */
function setup(override?: Handler) {
  const log: string[] = [];
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const method = init?.method ?? "GET";
    const url = String(input);
    log.push(`${method} ${url}`);
    const response = override?.(method, url);
    if (response !== undefined) return Promise.resolve(response);
    if (method === "POST") {
      return Promise.resolve(json({ entities: [lineOut([[5, 5], [6, 6]], CREATED_ID)] }, 201));
    }
    if (method === "PATCH") return Promise.resolve(json({ ...STRETCHED, candidates: null }));
    return Promise.resolve(json({ entities: [STRETCHED] }));
  });
  const api = createApiClient({ fetch: fetchMock });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, [lineOut([[0, 0], [3, 0]])]);
  return { log, fetchMock, api, store };
}

function addLocalLine(store: ReturnType<typeof createEditorStore>): string {
  return store.getState().addEntity({
    type: "line",
    layer: "L_STRUCTURE",
    geometry: { kind: "line", coords_m: [[5, 5], [6, 6]] },
  });
}

describe("runServerOperation", () => {
  it("flushes the queue, sends the PATCH and loads the server's entities", async () => {
    const { log, fetchMock, api, store } = setup();
    addLocalLine(store);

    const result = await runServerOperation(api, store, { revisionId: REVISION_ID }, LINE_ID, BODY);

    expect(result.ok).toBe(true);
    expect(log).toEqual([
      `POST ${ENTITIES_URL}`,
      `PATCH /api/v1/entities/${LINE_ID}`,
      `GET ${ENTITIES_URL}`,
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1]?.[1]?.body as string)).toEqual(BODY);
    expect(store.getState().entities.byId[LINE_ID]?.geometry).toEqual(STRETCHED.geometry);
    expect(store.getState().entities.order).toEqual([LINE_ID]);
    expect(store.getState().history.past).toEqual([]);
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("sends the PATCH to the server id of an entity drawn locally", async () => {
    const { log, api, store } = setup();
    const local = addLocalLine(store);

    await runServerOperation(api, store, { revisionId: REVISION_ID }, local, BODY);

    expect(log[1]).toBe(`PATCH /api/v1/entities/${CREATED_ID}`);
  });

  it("sends no PATCH when the queue cannot be drained", async () => {
    const holder = { user_id: "u2", display_name: "Ana", expires_at: "2999-01-01T00:00:00Z" };
    const { log, api, store } = setup((method) =>
      method === "POST" ? json({ title: "Conflict", code: "V-12", holder }, 409) : undefined,
    );
    addLocalLine(store);

    const result = await runServerOperation(api, store, { revisionId: REVISION_ID }, LINE_ID, BODY);

    expect(result).toMatchObject({ ok: false, message: v12Message("Ana") });
    expect(log).toEqual([`POST ${ENTITIES_URL}`]);
    expect(store.getState().queue.ops).toHaveLength(1);
  });

  it("gives up while another flush is in flight", async () => {
    const { log, api, store } = setup();
    addLocalLine(store);
    store.getState().beginSync();

    const result = await runServerOperation(api, store, { revisionId: REVISION_ID }, LINE_ID, BODY);

    expect(result).toEqual({ ok: false, message: SERVER_OPERATION_TEXT.busy });
    expect(log).toEqual([]);
  });

  it("passes a 422 V-04 through as the V-04 message and keeps the store", async () => {
    const { api, store } = setup((method) =>
      method === "PATCH" ? json({ title: "Unprocessable", code: "V-04" }, 422) : undefined,
    );
    const before = store.getState().entities;

    const result = await runServerOperation(api, store, { revisionId: REVISION_ID }, LINE_ID, BODY);

    expect(result).toMatchObject({ ok: false, message: DISTANCE_MESSAGE });
    expect(store.getState().entities).toBe(before);
  });

  it("sends edits queued during the PATCH before it reloads", async () => {
    let store: ReturnType<typeof createEditorStore> | null = null;
    const setupResult = setup((method) => {
      if (method === "PATCH" && store !== null) addLocalLine(store);
      return undefined;
    });
    store = setupResult.store;

    const context = { revisionId: REVISION_ID };
    const result = await runServerOperation(setupResult.api, store, context, LINE_ID, BODY);

    expect(result.ok).toBe(true);
    expect(setupResult.log).toEqual([
      `PATCH /api/v1/entities/${LINE_ID}`,
      `GET ${ENTITIES_URL}`,
      `POST ${ENTITIES_URL}`,
      `GET ${ENTITIES_URL}`,
    ]);
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("reports an applied operation whose reload failed", async () => {
    const { api, store } = setup((method) =>
      method === "GET" ? json({ title: "Server Error" }, 500) : undefined,
    );

    const result = await runServerOperation(api, store, { revisionId: REVISION_ID }, LINE_ID, BODY);

    expect(result).toMatchObject({ ok: false, message: SERVER_OPERATION_TEXT.reloadFailed });
  });
});

describe("serverOperationMessage", () => {
  const problem = (status: number, extra: Record<string, unknown> = {}) =>
    new ApiError(status, { title: "x", ...extra });

  it("maps 422 codes, with per-operation messages", () => {
    expect(serverOperationMessage(problem(422, { code: "V-04" }))).toBe(DISTANCE_MESSAGE);
    expect(serverOperationMessage(problem(422))).toBe(SERVER_OPERATION_TEXT.invalid);
    const custom = { "V-06": "No se cruzan." };
    expect(serverOperationMessage(problem(422, { code: "V-06" }), custom)).toBe("No se cruzan.");
  });

  it("maps the 409 conflicts", () => {
    const holder = { user_id: "u2", display_name: "Ana", expires_at: "2999-01-01T00:00:00Z" };
    expect(serverOperationMessage(problem(409, { code: "V-12", holder }))).toBe(v12Message("Ana"));
    expect(serverOperationMessage(problem(409, { code: "V-12" }))).toBe(
      v12Message(SERVER_OPERATION_TEXT.otherUser),
    );
    expect(serverOperationMessage(problem(409, { code: "lease_required" }))).toBe(
      PLAN_MESSAGES.leaseLost,
    );
    expect(serverOperationMessage(problem(409, { code: "V-10" }))).toBe(V10_MESSAGE);
    expect(serverOperationMessage(problem(409, { code: "constraint_removal_required" }))).toBe(
      SERVER_OPERATION_TEXT.constraintRemoval,
    );
    expect(serverOperationMessage(problem(409))).toBe(PLAN_MESSAGES.notEditable);
  });

  it("maps anything else to a generic message", () => {
    expect(serverOperationMessage(problem(403))).toBe(PLAN_MESSAGES.forbidden);
    expect(serverOperationMessage(problem(500))).toBe(SERVER_OPERATION_TEXT.failed);
    expect(serverOperationMessage(new TypeError("network"))).toBe(SERVER_OPERATION_TEXT.failed);
  });
});
