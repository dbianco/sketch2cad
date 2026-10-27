import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { v12Message } from "../../features/plans/messages";
import { EditorWorkspace } from "../EditorWorkspace";
import type { Catalog } from "../canvas/EditorCanvas";
import type { Viewport } from "../canvas/viewport";
import catalogJson from "../fixtures/catalog-default-1.json";
import { createSnapSettingsStore } from "../panels/SnapSettings";
import { createPlanStore } from "../plan/planStore";
import { createEditorStore } from "../store/entities";
import type { EntityOut, Point2 } from "../store/types";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";
import { DISTANCE_MESSAGE } from "../util/parseDistance";
import { FIX_LENGTH_TEXT, fixLengthSummary } from "./FixLengthTool";

const CATALOG = catalogJson as Catalog;
const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const LINE_ID = "00000000-0000-4000-8000-0000000000e1";
const CREATED_ID = "00000000-0000-4000-8000-0000000000e2";
const PATCH_URL = `/api/v1/entities/${LINE_ID}`;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const ORIGIN = { left: 40, top: 30 };
const POLYLINE: Point2[] = [[0, 0], [3, 0], [3, 2]];
const STRETCHED: Point2[] = [[0, 0], [4.2, 0], [4.2, 2]];

function polylineOut(coords: Point2[], id = LINE_ID): EntityOut {
  return {
    id,
    type: "polyline",
    layer: "L_STRUCTURE",
    geometry: { kind: "polyline", coords_m: coords, closed: false },
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

/** Server whose fix_length stretches the first segment to 4.2 m; `patch` replaces its answer. */
function server(patch?: Response) {
  return (call: Call): Response => {
    if (call.method === "PATCH") {
      return patch ?? json({ ...polylineOut(STRETCHED), candidates: null });
    }
    if (call.method === "GET" && call.url === ENTITIES_URL) {
      return json({ entities: [polylineOut(STRETCHED)] });
    }
    if (call.method === "POST" && call.url === ENTITIES_URL) {
      return json({ entities: [polylineOut([[5, 5], [6, 6]], CREATED_ID)] }, 201);
    }
    return json({ title: "Not Found" }, 404);
  };
}

interface SetupOptions {
  patch?: Response;
  readOnly?: boolean;
  tool?: ToolId;
  locked?: boolean;
  lockedLayers?: string[];
}

function setup(options: SetupOptions = {}) {
  const calls: Call[] = [];
  const handler = server(options.patch);
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const call: Call = {
      method: init?.method ?? "GET",
      url: String(input),
      body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
    };
    calls.push(call);
    return Promise.resolve(handler(call));
  });
  const api = createApiClient({ fetch: fetchMock });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, [polylineOut(POLYLINE)]);
  const ui = createEditorUiStore({
    activeTool: options.tool ?? "fixlength",
    layerFlags: Object.fromEntries(
      [...(options.locked === true ? ["L_STRUCTURE"] : []), ...(options.lockedLayers ?? [])].map(
        (name) => [name, { visible: true, locked: true }],
      ),
    ),
  });
  const plan = createPlanStore({ revisionId: REVISION_ID, readOnly: options.readOnly ?? false });
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
  /** Click (press and release) at a world point. */
  const click = (p: Point2) => {
    if (viewport === null) throw new Error("No viewport yet");
    const { centerX, centerY, scale, width, height } = viewport;
    const clientX = (p[0] - centerX) * scale + width / 2 + ORIGIN.left;
    const clientY = (centerY - p[1]) * scale + height / 2 + ORIGIN.top;
    fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX, clientY });
    fireEvent.pointerUp(svg, { pointerId: 1, button: 0, clientX, clientY });
  };
  return { calls, store, ui, svg, click };
}

function field(): HTMLInputElement {
  return screen.getByRole("textbox", { name: FIX_LENGTH_TEXT.field });
}

async function enter(value: string) {
  fireEvent.change(field(), { target: { value } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: FIX_LENGTH_TEXT.apply }));
    await new Promise((resolve) => setTimeout(resolve));
  });
}

afterEach(cleanup);

describe("fix-length tool", () => {
  it('"4,20" sends fix_length with value_m 4.2 and loads the server geometry', async () => {
    const { calls, store, click } = setup();
    click([1.5, 0]);
    expect(screen.getByTestId("fix-length-segment")).toBeTruthy();
    expect(screen.getByText(FIX_LENGTH_TEXT.pickAnchor)).toBeTruthy();
    click([0, 0]);
    expect(screen.getByText(FIX_LENGTH_TEXT.current(3))).toBeTruthy();
    const anchor = FIX_LENGTH_TEXT.endRole(1, FIX_LENGTH_TEXT.anchor);
    expect(screen.getByRole("button", { name: anchor })).toBeTruthy();
    const free = FIX_LENGTH_TEXT.endRole(2, FIX_LENGTH_TEXT.free);
    expect(screen.getByRole("button", { name: free })).toBeTruthy();

    await enter("4,20");

    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      `PATCH ${PATCH_URL}`,
      `GET ${ENTITIES_URL}`,
    ]);
    const body = calls[0]?.body as Record<string, unknown>;
    expect(body).toEqual({
      operation: "fix_length",
      anchor_vertex: 0,
      free_vertex: 1,
      value_m: 4.2,
    });
    expect(typeof body.value_m).toBe("number");
    expect(store.getState().entities.byId[LINE_ID]?.geometry).toEqual({
      kind: "polyline",
      coords_m: STRETCHED,
      closed: false,
    });
    expect(FIX_LENGTH_TEXT.done(4.2)).toBe("Longitud fijada en 4,20 m");
    expect(screen.getByText(FIX_LENGTH_TEXT.done(4.2)).getAttribute("role")).toBe("status");
    expect(screen.queryByTestId("fix-length-segment")).toBeNull();
  });

  it('"0" shows the V-04 message and sends nothing', async () => {
    const { calls, click } = setup();
    click([1.5, 0]);
    click([0, 0]);

    await enter("0");

    expect(screen.getByRole("alert").textContent).toBe(
      "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50.",
    );
    expect(calls).toEqual([]);
  });

  it.each(["-3", "abc", "1e3", "1.000,50", ""])("%j is rejected locally", async (value) => {
    const { calls, click } = setup();
    click([1.5, 0]);
    click([0, 0]);

    await enter(value);

    expect(screen.getByRole("alert").textContent).toBe(DISTANCE_MESSAGE);
    expect(calls).toEqual([]);
  });

  it("the other end as anchor sends it as anchor_vertex, chosen with its button", async () => {
    const { calls, click } = setup();
    click([3, 1]);
    fireEvent.click(screen.getByRole("button", { name: FIX_LENGTH_TEXT.end(2) }));
    expect(screen.getByText(FIX_LENGTH_TEXT.current(2))).toBeTruthy();

    await enter("4.5");

    expect(calls[0]?.body).toEqual({
      operation: "fix_length",
      anchor_vertex: 2,
      free_vertex: 1,
      value_m: 4.5,
    });
  });

  it("works with the keyboard cursor: Enter picks the segment, then the anchor", async () => {
    const { calls, svg } = setup();
    svg.focus();
    // The cursor starts at the view centre (1.5, 1); the grid step is 0.1 m, Alt moves 10 steps.
    fireEvent.keyDown(svg, { key: "ArrowDown", altKey: true });
    fireEvent.keyDown(svg, { key: "Enter" });
    expect(screen.getByTestId("fix-length-segment")).toBeTruthy();
    fireEvent.keyDown(svg, { key: "ArrowLeft", altKey: true });
    for (let i = 0; i < 5; i += 1) fireEvent.keyDown(svg, { key: "ArrowLeft" });
    fireEvent.keyDown(svg, { key: "Enter" });

    await enter("4,20");

    expect(calls[0]?.body).toMatchObject({ anchor_vertex: 0, free_vertex: 1, value_m: 4.2 });
  });

  it("flushes the pending queue before the PATCH", async () => {
    const { calls, store, click } = setup();
    store.getState().addEntity({
      type: "line",
      layer: "L_STRUCTURE",
      geometry: { kind: "line", coords_m: [[5, 5], [6, 6]] },
    });
    click([1.5, 0]);
    click([0, 0]);

    await enter("4,20");

    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      `POST ${ENTITIES_URL}`,
      `PATCH ${PATCH_URL}`,
      `GET ${ENTITIES_URL}`,
    ]);
  });

  it("shows the server's V-04 and keeps the segment picked", async () => {
    const { store, click } = setup({ patch: json({ title: "x", code: "V-04" }, 422) });
    click([1.5, 0]);
    click([0, 0]);

    await enter("4,20");

    expect(screen.getByRole("alert").textContent).toBe(DISTANCE_MESSAGE);
    expect(screen.getByTestId("fix-length-segment")).toBeTruthy();
    expect(store.getState().entities.byId[LINE_ID]?.geometry).toMatchObject({ coords_m: POLYLINE });
  });

  it("shows the 409 lease message", async () => {
    const holder = { user_id: "u2", display_name: "Ana", expires_at: "2999-01-01T00:00:00Z" };
    const { click } = setup({ patch: json({ title: "x", code: "V-12", holder }, 409) });
    click([1.5, 0]);
    click([0, 0]);

    await enter("4,20");

    expect(screen.getByRole("alert").textContent).toBe(v12Message("Ana"));
  });

  it("Esc cancels the picked segment", () => {
    const { click } = setup();
    click([1.5, 0]);
    click([0, 0]);
    fireEvent.keyDown(field(), { key: "Escape" });
    expect(screen.queryByTestId("fix-length-segment")).toBeNull();
    expect(screen.getByText(FIX_LENGTH_TEXT.pickSegment)).toBeTruthy();
  });

  it("sends the locked layers and reports the connected vertices that moved", async () => {
    const other = "abcdef12-0000-4000-8000-000000000000";
    const connected = {
      tolerance_m: 0.1,
      moved: [{ entity_id: other, vertex: 0, from_m: [3, 0], to_m: [4.2, 0] }],
      pinned: [],
      length_conflicts: [],
    };
    const patch = json({ ...polylineOut(STRETCHED), candidates: null, connected });
    const { calls, click } = setup({ patch, lockedLayers: ["L_ACCESS", "L_ANNOTATIONS"] });
    click([1.5, 0]);
    click([0, 0]);

    await enter("4,20");

    expect((calls[0]?.body as Record<string, unknown>).locked_layers).toEqual([
      "L_ACCESS",
      "L_ANNOTATIONS",
    ]);
    const notice = screen.getByText(/Se movió 1 vértice conectado/);
    expect(notice.textContent).toBe(
      "Longitud fijada en 4,20 m. Se movió 1 vértice conectado (1 entidad: abcdef12).",
    );
    expect(notice.getAttribute("role")).toBe("status");
  });

  it("warns with ids and metres about pinned vertices and broken declared lengths", () => {
    const summary = fixLengthSummary(4.8, {
      tolerance_m: 0.1,
      moved: [
        { entity_id: "11111111-a", vertex: 0, from_m: [4, 3], to_m: [4.8, 3] },
        { entity_id: "22222222-b", vertex: 1, from_m: [4, 3], to_m: [4.8, 3] },
      ],
      pinned: [{ entity_id: "33333333-c", vertex: 0, layer: "L_ACCESS", gap_m: 0.8 }],
      length_conflicts: [
        {
          entity_id: "11111111-a",
          segment: 0,
          declared_m: 3,
          actual_m: 3.104,
          residual_m: 0.104,
        },
      ],
    });
    expect(summary.warning).toBe(true);
    expect(summary.text).toBe(
      "Longitud fijada en 4,80 m. Se movieron 2 vértices conectados (2 entidades: 11111111, " +
        "22222222). 1 vértice conectado en capas bloqueadas (L_ACCESS) no se movió: queda un " +
        "hueco de hasta 0,800 m. La longitud declarada 3,00 m del segmento 1 de 11111111 ya no " +
        "se cumple: mide 3,104 m (diferencia 0,104 m).",
    );
    expect(fixLengthSummary(4.8, null)).toEqual({
      text: "Longitud fijada en 4,80 m",
      warning: false,
    });
  });

  it("does not pick segments on a locked layer", () => {
    const { click } = setup({ locked: true });
    click([1.5, 0]);
    expect(screen.queryByTestId("fix-length-segment")).toBeNull();
  });

  it("is in the palette with the D shortcut", () => {
    const { ui } = setup({ tool: "select" });
    fireEvent.keyDown(document, { key: "d" });
    expect(ui.getState().activeTool).toBe("fixlength");
    const button = screen.getByRole("button", { name: "Fijar longitud" });
    expect(button.getAttribute("aria-pressed")).toBe("true");
  });

  it("is disabled in read-only mode", () => {
    const { calls, click } = setup({ readOnly: true });
    const button = screen.getByRole("button", { name: "Fijar longitud" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    click([1.5, 0]);
    expect(screen.queryByTestId("fix-length-segment")).toBeNull();
    expect(screen.queryByText(FIX_LENGTH_TEXT.pickSegment)).toBeNull();
    expect(calls).toEqual([]);
  });
});
