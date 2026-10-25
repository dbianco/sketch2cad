import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { v12Message } from "../../features/calibration/messages";
import { EditorWorkspace } from "../EditorWorkspace";
import { NorthArrow, northArrowTransform } from "../canvas/NorthArrow";
import type { Catalog } from "../canvas/EditorCanvas";
import { screenToWorld, type Viewport } from "../canvas/viewport";
import catalogJson from "../fixtures/catalog-default-1.json";
import { directionDeg, localToPaper, worldAxisAngle, type LocalFrame } from "../frame/frame";
import { createSnapSettingsStore } from "../panels/SnapSettings";
import { createPlanStore, type LeaseOut, type PlanOut, type PlanState } from "../plan/planStore";
import { createEditorStore } from "../store/entities";
import type { EntityOut, Point2 } from "../store/types";
import { createEditorUiStore } from "../ui/editorUi";
import { LOCAL_FRAME_TEXT } from "./LocalFrameTool";

const CATALOG = catalogJson as Catalog;
const PLAN_ID = "00000000-0000-4000-8000-0000000000p1";
const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const ENTITY_ID = "00000000-0000-4000-8000-0000000000e1";
const USER_ID = "00000000-0000-4000-8000-0000000000u1";
const ORIGIN = { left: 40, top: 30 };
const FRAME_URL = `/api/v1/plans/${PLAN_ID}/local-frame`;
const LEASE_URL = `/api/v1/plans/${PLAN_ID}/lease`;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const FIRST: LocalFrame = { origin_paper_mm: [12.5, 30], x_axis_angle_deg: 0, north_angle_deg: 0 };
const LEASE: LeaseOut = {
  user_id: USER_ID,
  display_name: "Técnico",
  expires_at: "2999-01-01T00:00:00Z",
};

function entityOut(coords: Point2[], id = ENTITY_ID): EntityOut {
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

function planOut(frame: LocalFrame | null): PlanOut {
  return {
    id: PLAN_ID,
    case_file_id: "00000000-0000-4000-8000-0000000000c1",
    created_at: "2026-10-25T00:00:00Z",
    current_revision: { id: REVISION_ID, number: 1, state: "draft" },
    current_revision_id: REVISION_ID,
    lease: LEASE,
    metric_status: "approximate",
    primary_source_id: "00000000-0000-4000-8000-0000000000s1",
    source_mode: "schematic_sketch",
    status: "EDITING",
    title: "Plano 1",
    validated_revision_id: null,
    calibration: {
      id: "00000000-0000-4000-8000-0000000000k1",
      accepted_at: "2026-10-25T00:00:00Z",
      accepted_by: USER_ID,
      as_metric: false,
      control_points: [],
      image_to_paper: null,
      local_frame: frame,
      method: "none",
      paper_scale: null,
      reprojection_error_mm: null,
    },
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

interface Call {
  method: string;
  url: string;
  body: unknown;
}

type Handler = (call: Call, index: number) => Response;

/** Server that echoes the frame of a PUT and returns `moved` entities on GET. */
function server(moved: EntityOut[] = [entityOut([[0, 0], [2.75, 0]])]): Handler {
  return (call) => {
    if (call.method === "PUT" && call.url === FRAME_URL) return json(planOut(call.body as LocalFrame));
    if (call.method === "GET" && call.url === ENTITIES_URL) return json({ entities: moved });
    if (call.method === "POST" && call.url === LEASE_URL) return json(LEASE);
    if (call.method === "POST" && call.url === ENTITIES_URL) {
      return json({ entities: [entityOut([[5, 5], [6, 6]], "00000000-0000-4000-8000-0000000000e2")] }, 201);
    }
    return json({ title: "Not Found" }, 404);
  };
}

function setup(options: { handler?: Handler; plan?: Partial<PlanState> } = {}) {
  const calls: Call[] = [];
  const handler = options.handler ?? server();
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const call: Call = {
      method: init?.method ?? "GET",
      url: String(input),
      body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
    };
    calls.push(call);
    return Promise.resolve(handler(call, calls.length - 1));
  });
  const api = createApiClient({ fetch: fetchMock });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, [entityOut([[1, 1], [3, 1]])]);
  const load = vi.spyOn(store.getState(), "load");
  const ui = createEditorUiStore({ activeTool: "frame" });
  const plan = createPlanStore({
    planId: PLAN_ID,
    revisionId: REVISION_ID,
    calibrated: true,
    paperScale: null,
    localFrame: FIRST,
    lease: LEASE,
    userId: USER_ID,
    ...options.plan,
  });
  let viewport: Viewport | null = null;
  render(
    <EditorWorkspace
      catalog={CATALOG}
      store={store}
      ui={ui}
      plan={plan}
      api={api}
      snap={createSnapSettingsStore(null)}
      width={800}
      height={600}
      onViewportChange={(next) => {
        viewport = next;
      }}
    />,
  );
  const svg = screen.getByTestId("editor-canvas") as unknown as SVGSVGElement;
  svg.getBoundingClientRect = () => new DOMRect(ORIGIN.left, ORIGIN.top, 800, 600);
  /** Canvas (local) point under a client position. */
  const local = (x: number, y: number): Point2 => {
    if (viewport === null) throw new Error("No viewport yet");
    return screenToWorld(viewport, [x - ORIGIN.left, y - ORIGIN.top]);
  };
  /** Client position of a canvas point. */
  const client = (p: Point2): [number, number] => {
    if (viewport === null) throw new Error("No viewport yet");
    const { centerX, centerY, scale, width, height } = viewport;
    return [
      (p[0] - centerX) * scale + width / 2 + ORIGIN.left,
      (centerY - p[1]) * scale + height / 2 + ORIGIN.top,
    ];
  };
  return { calls, store, load, ui, plan, svg, local, client };
}

function press(svg: Element, x: number, y: number) {
  fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: x, clientY: y });
}
function move(svg: Element, x: number, y: number) {
  fireEvent.pointerMove(svg, { pointerId: 1, button: 0, clientX: x, clientY: y });
}
function release(svg: Element, x: number, y: number) {
  fireEvent.pointerUp(svg, { pointerId: 1, button: 0, clientX: x, clientY: y });
}

async function applyPlacement() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: LOCAL_FRAME_TEXT.applyPlacement }));
    await Promise.resolve();
  });
}

async function applyNorth(value: string) {
  fireEvent.change(screen.getByRole("spinbutton", { name: LOCAL_FRAME_TEXT.northField }), {
    target: { value },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: LOCAL_FRAME_TEXT.applyNorth }));
    await Promise.resolve();
  });
}

function needle(): string | null {
  return screen.getByTestId("north-arrow-needle").getAttribute("transform");
}

afterEach(cleanup);

describe("north arrow", () => {
  it("rotates the needle counter-clockwise by the North angle", () => {
    render(<NorthArrow northDeg={30} />);
    expect(needle()).toBe("rotate(-30)");
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("30°");
    expect(northArrowTransform(0)).toBe("rotate(0)");
    expect(northArrowTransform(-45)).toBe("rotate(45)");
    expect(northArrowTransform(272.5)).toBe("rotate(-272.5)");
  });
});

describe("local frame tool", () => {
  it("changing North sends one PUT, reloads nothing and turns the arrow", async () => {
    const { calls, store, load, plan } = setup();
    const before = store.getState().entities;
    expect(needle()).toBe("rotate(0)");

    await applyNorth("30");

    expect(await screen.findByText(LOCAL_FRAME_TEXT.northDone)).toBeTruthy();
    expect(calls).toEqual([
      { method: "PUT", url: FRAME_URL, body: { ...FIRST, north_angle_deg: 30 } },
    ]);
    expect(load).not.toHaveBeenCalled();
    expect(store.getState().entities).toBe(before);
    expect(plan.getState().localFrame?.north_angle_deg).toBe(30);
    expect(needle()).toBe("rotate(-30)");
  });

  it("a click sets the origin in paper mm, and applying it reloads the entities", async () => {
    const { calls, store, load, svg, local, plan } = setup();
    press(svg, 200, 150);
    release(svg, 200, 150);
    const clicked = local(200, 150);
    expect(screen.getByTestId("frame-origin-preview")).toBeTruthy();

    await applyPlacement();

    expect(await screen.findByText(LOCAL_FRAME_TEXT.done)).toBeTruthy();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      `PUT ${FRAME_URL}`,
      `GET ${ENTITIES_URL}`,
    ]);
    expect(calls[0]?.body).toEqual({
      origin_paper_mm: localToPaper(FIRST, 100, clicked),
      x_axis_angle_deg: 0,
      north_angle_deg: 0,
    });
    expect(load).toHaveBeenCalledTimes(1);
    const entity = store.getState().entities.byId[ENTITY_ID];
    expect(entity?.geometry).toEqual({ kind: "line", coords_m: [[0, 0], [2.75, 0]] });
    expect(store.getState().history.past).toHaveLength(0);
    expect(plan.getState().localFrame?.origin_paper_mm).toEqual(localToPaper(FIRST, 100, clicked));
    expect(screen.queryByTestId("frame-origin-preview")).toBeNull();
  });

  it("a drag from the origin sets the +X angle with a live preview line", async () => {
    const { calls, svg, local, client } = setup();
    const [ox, oy] = client([0, 0]);
    press(svg, ox + 2, oy + 1);
    move(svg, ox + 120, oy - 70);
    expect(screen.getByTestId("frame-axis-preview")).toBeTruthy();
    release(svg, ox + 160, oy - 90);
    const angle = worldAxisAngle(FIRST, directionDeg([0, 0], local(ox + 160, oy - 90)));

    await applyPlacement();

    expect(await screen.findByText(LOCAL_FRAME_TEXT.done)).toBeTruthy();
    // Pressing on the current origin keeps it exactly: only the axis changes.
    expect(calls[0]?.body).toEqual({ ...FIRST, x_axis_angle_deg: angle });
    expect(angle).toBeGreaterThan(20);
    expect(angle).toBeLessThan(40);
    expect(calls[1]?.method).toBe("GET");
  });

  it("flushes the queued operations before changing the frame", async () => {
    const { calls, store, svg } = setup();
    store.getState().addEntity({
      type: "line",
      layer: "L_STRUCTURE",
      geometry: { kind: "line", coords_m: [[5, 5], [6, 6]] },
      attributes: {},
    });
    press(svg, 200, 150);
    release(svg, 200, 150);

    await applyPlacement();

    expect(await screen.findByText(LOCAL_FRAME_TEXT.done)).toBeTruthy();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      `POST ${ENTITIES_URL}`,
      `PUT ${FRAME_URL}`,
      `GET ${ENTITIES_URL}`,
    ]);
    expect(store.getState().queue.ops).toHaveLength(0);
  });

  it("aborts the change when the queue cannot be drained", async () => {
    const base = server();
    const { calls, store, load, svg } = setup({
      handler: (call, index) =>
        call.method === "POST" ? json({ title: "Server Error" }, 500) : base(call, index),
    });
    store.getState().addEntity({
      type: "line",
      layer: "L_STRUCTURE",
      geometry: { kind: "line", coords_m: [[5, 5], [6, 6]] },
      attributes: {},
    });
    press(svg, 200, 150);
    release(svg, 200, 150);

    await applyPlacement();

    expect((await screen.findByRole("alert")).textContent).toBe(LOCAL_FRAME_TEXT.flushFailed);
    expect(calls.map((call) => call.method)).toEqual(["POST"]);
    expect(load).not.toHaveBeenCalled();
    expect(store.getState().queue.ops).toHaveLength(1);
  });

  it("takes the edit lease before an origin change when it does not hold it", async () => {
    const { calls, svg } = setup({ plan: { lease: null } });
    press(svg, 200, 150);
    release(svg, 200, 150);

    await applyPlacement();

    expect(await screen.findByText(LOCAL_FRAME_TEXT.done)).toBeTruthy();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      `POST ${LEASE_URL}`,
      `PUT ${FRAME_URL}`,
      `GET ${ENTITIES_URL}`,
    ]);
  });

  it("shows the V-12 message when another user holds the lease", async () => {
    const holder = { user_id: "other", display_name: "Ana Ruiz", expires_at: LEASE.expires_at };
    const { calls, load } = setup({
      handler: () => json({ title: "Conflict", status: 409, code: "V-12", holder }, 409),
    });

    await applyNorth("10");

    expect((await screen.findByRole("alert")).textContent).toBe(v12Message("Ana Ruiz"));
    expect(calls).toHaveLength(1);
    expect(load).not.toHaveBeenCalled();
  });

  it("retakes a missing lease once and explains when it is still required", async () => {
    const leaseRequired = json({ title: "Conflict", status: 409, code: "lease_required" }, 409);
    const { calls } = setup({
      handler: (call) =>
        call.method === "POST" ? json(LEASE) : leaseRequired.clone(),
    });

    await applyNorth("10");

    expect((await screen.findByRole("alert")).textContent).toBe(LOCAL_FRAME_TEXT.leaseRequired);
    expect(calls.map((call) => call.method)).toEqual(["PUT", "POST", "PUT"]);
  });

  it("asks for a calibration first and sends nothing without one", async () => {
    const { calls } = setup({ plan: { calibrated: false, localFrame: null } });

    await applyNorth("10");

    expect((await screen.findByRole("alert")).textContent).toBe(LOCAL_FRAME_TEXT.noCalibration);
    expect(calls).toHaveLength(0);
  });

  it("explains a 409 without code from the server", async () => {
    const { svg } = setup({ handler: () => json({ title: "Conflict", status: 409 }, 409) });
    press(svg, 200, 150);
    release(svg, 200, 150);

    await applyPlacement();

    expect((await screen.findByRole("alert")).textContent).toBe(LOCAL_FRAME_TEXT.notEditable);
  });

  it("drops the pending origin with Escape", () => {
    const { svg, ui } = setup();
    press(svg, 200, 150);
    release(svg, 200, 150);
    expect(ui.getState().draft).toHaveLength(1);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(ui.getState().draft).toHaveLength(0);
  });
});
