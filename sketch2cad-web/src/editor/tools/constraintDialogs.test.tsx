import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
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
import { BASELINE_TEXT } from "./BaselineOffsetDialog";
import { CONSTRAINT_TEXT } from "./ConstraintTool";
import { TRIANGULATION_TEXT } from "./TriangulationDialog";

const CATALOG = catalogJson as Catalog;
const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const P_ID = "00000000-0000-4000-8000-0000000000p1";
const A_ID = "00000000-0000-4000-8000-0000000000a1";
const B_ID = "00000000-0000-4000-8000-0000000000b1";
const LINE_ID = "00000000-0000-4000-8000-0000000000e2";
const PATCH_URL = `/api/v1/entities/${P_ID}`;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const PATCH_THEN_RELOAD = [`PATCH ${PATCH_URL}`, `GET ${ENTITIES_URL}`];
const ORIGIN = { left: 40, top: 30 };
// 3-4-5 triangle (server fixture "3-4-5 nearest"): candidates (1.8, 2.4) left and (1.8, -2.4).
const A: Point2 = [0, 0];
const B: Point2 = [5, 0];
const P: Point2 = [2, 2];
const V06 = "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas.";
const V07 = "Esta referencia no es métrica o depende del punto que intentas fijar.";

function pointOut(id: string, at: Point2, layer = "L_EVIDENCE"): EntityOut {
  return {
    id,
    type: "point",
    layer,
    geometry: { kind: "point", coords_m: [at] },
    attributes: {},
    confidence: null,
    constraint: null,
    created_by: null,
    deleted_at: null,
    discrepancy: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "declared_dimension",
    model_version: null,
    review_status: "accepted",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "manual" },
    updated_at: "2026-10-25T00:00:00Z",
  };
}

const LOCKED = { visible: true, locked: true };

/** The point to fix at `p` and the references A and B. */
function entitiesAt(p: Point2): EntityOut[] {
  return [pointOut(P_ID, p), pointOut(A_ID, A, "L_STRUCTURE"), pointOut(B_ID, B, "L_STRUCTURE")];
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

const FIXED: Point2 = [1.8, 2.4];

function server(patch?: Response) {
  return (call: Call): Response => {
    const moved = entitiesAt(FIXED);
    if (call.method === "PATCH") {
      const candidates = [[1.8, 2.4], [1.8, -2.4]];
      return patch ?? json({ ...moved[0], candidates, chosen_candidate: 0 });
    }
    if (call.method === "GET" && call.url === ENTITIES_URL) return json({ entities: moved });
    if (call.method === "POST" && call.url === ENTITIES_URL) {
      const geometry = { kind: "line", coords_m: [[5, 5], [6, 6]] };
      return json({ entities: [{ ...pointOut(LINE_ID, [9, 9]), type: "line", geometry }] }, 201);
    }
    return json({ title: "Not Found" }, 404);
  };
}

interface SetupOptions {
  patch?: Response;
  tool?: ToolId;
  readOnly?: boolean;
  lockedTarget?: boolean;
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
  store.getState().load(REVISION_ID, entitiesAt(P));
  const ui = createEditorUiStore({
    activeTool: options.tool ?? "triangulate",
    layerFlags: options.lockedTarget === true ? { L_EVIDENCE: LOCKED } : {},
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

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function apply() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: CONSTRAINT_TEXT.apply }));
    await new Promise((resolve) => setTimeout(resolve));
  });
}

function candidates() {
  return screen.queryAllByTestId("constraint-candidate");
}

/** Pick P, A and B and type the distances. */
function triangulateSetup(options: SetupOptions = {}, dA = "3", dB = "4,0") {
  const env = setup(options);
  env.click(P);
  env.click(A);
  env.click(B);
  type(TRIANGULATION_TEXT.distanceA, dA);
  type(TRIANGULATION_TEXT.distanceB, dB);
  return env;
}

afterEach(cleanup);

describe("triangulation dialog", () => {
  it("renders 2 candidate markers with the nearest selected", () => {
    triangulateSetup();
    const panel = screen.getByRole("complementary", { name: TRIANGULATION_TEXT.title });
    const chips = within(panel).getAllByTestId("constraint-chip");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      expect.stringContaining("Punto a fijar: Punto en x 2,00 m · y 2,00 m"),
      expect.stringContaining("Referencia A: Punto en x 0,00 m · y 0,00 m"),
      expect.stringContaining("Referencia B: Punto en x 5,00 m · y 0,00 m"),
    ]);
    const markers = candidates();
    expect(markers).toHaveLength(2);
    expect(markers.map((m) => m.getAttribute("data-selected"))).toEqual(["true", "false"]);
    expect(markers.map((m) => m.getAttribute("data-nearest"))).toEqual(["true", "false"]);
    expect(markers[0]?.getAttribute("d")).toBe("M1.8,-2.4h0");
    expect(screen.getByText(TRIANGULATION_TEXT.two)).toBeTruthy();
    const nearest = screen.getByRole("radio", { name: TRIANGULATION_TEXT.nearest });
    expect(nearest).toHaveProperty("checked", true);
  });

  it("sends triangulate with solution nearest and reloads the server position", async () => {
    const { calls, store } = triangulateSetup();
    await apply();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual(PATCH_THEN_RELOAD);
    expect(calls[0]?.body).toEqual({
      operation: "triangulate",
      ref_a: A_ID,
      ref_b: B_ID,
      d_a_m: 3,
      d_b_m: 4,
      solution: "nearest",
    });
    const geometry = store.getState().entities.byId[P_ID]?.geometry;
    expect(geometry).toEqual({ kind: "point", coords_m: [FIXED] });
    expect(screen.getByText(TRIANGULATION_TEXT.done).getAttribute("role")).toBe("status");
    expect(candidates()).toHaveLength(0);
  });

  it("choosing the non-nearest candidate sends solution alt", async () => {
    const { calls } = triangulateSetup();
    fireEvent.click(screen.getByRole("radio", { name: TRIANGULATION_TEXT.alt }));
    expect(candidates().map((m) => m.getAttribute("data-selected"))).toEqual(["false", "true"]);
    await apply();
    expect(calls[0]?.body).toMatchObject({ solution: "alt" });
  });

  it("a click on the other candidate marker selects it", async () => {
    const { calls, click } = triangulateSetup();
    click([1.8, -2.4]);
    const alt = screen.getByRole("radio", { name: TRIANGULATION_TEXT.alt });
    expect(alt).toHaveProperty("checked", true);
    await apply();
    expect(calls[0]?.body).toMatchObject({ solution: "alt" });
  });

  it("the nearest follows the point's position: below the baseline the right one", () => {
    const env = setup();
    env.store.getState().load(REVISION_ID, entitiesAt([2, -2]));
    env.click([2, -2]);
    env.click(A);
    env.click(B);
    type(TRIANGULATION_TEXT.distanceA, "3");
    type(TRIANGULATION_TEXT.distanceB, "4");
    expect(candidates().map((m) => m.getAttribute("data-selected"))).toEqual(["false", "true"]);
  });

  it("tangent circles give one candidate", async () => {
    const { calls } = triangulateSetup({}, "2", "3");
    expect(candidates()).toHaveLength(1);
    expect(screen.getByText(TRIANGULATION_TEXT.one)).toBeTruthy();
    expect(screen.queryByRole("radio")).toBeNull();
    await apply();
    expect(calls[0]?.body).toMatchObject({ solution: "nearest" });
  });

  it("circles that do not cross show V-06 locally and send nothing", async () => {
    const { calls } = triangulateSetup({}, "1", "1");
    expect(candidates()).toHaveLength(0);
    expect(screen.getByRole("alert").textContent).toBe(V06);
    await apply();
    expect(screen.getByRole("alert").textContent).toBe(V06);
    expect(calls).toEqual([]);
  });

  it("editing a distance clears the message of the previous attempt", async () => {
    triangulateSetup({}, "1", "1");
    await apply();
    expect(screen.getByRole("alert").textContent).toBe(V06);
    type(TRIANGULATION_TEXT.distanceA, "3");
    type(TRIANGULATION_TEXT.distanceB, "4");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(candidates()).toHaveLength(2);
  });

  it("a 422 V-06 from the server renders the PRD message", async () => {
    triangulateSetup({ patch: json({ title: "x", code: "V-06" }, 422) });
    await apply();
    expect(screen.getByRole("alert").textContent).toBe(
      "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas.",
    );
    expect(candidates()).toHaveLength(2);
  });

  it("a 422 V-07 from the server renders the PRD message", async () => {
    triangulateSetup({ patch: json({ title: "x", code: "V-07" }, 422) });
    await apply();
    expect(screen.getByRole("alert").textContent).toBe(V07);
  });

  it.each(["0", "-3", "abc", ""])("distance %j shows V-04 and sends nothing", async (value) => {
    const { calls } = triangulateSetup({}, value);
    expect(candidates()).toHaveLength(0);
    await apply();
    expect(screen.getByRole("alert").textContent).toBe(DISTANCE_MESSAGE);
    expect(calls).toEqual([]);
  });

  it("flushes the pending queue before the PATCH", async () => {
    const { calls, store } = triangulateSetup();
    store.getState().addEntity({
      type: "line",
      layer: "L_STRUCTURE",
      geometry: { kind: "line", coords_m: [[5, 5], [6, 6]] },
    });
    await apply();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      `POST ${ENTITIES_URL}`,
      `PATCH ${PATCH_URL}`,
      `GET ${ENTITIES_URL}`,
    ]);
  });

  it("works with the keyboard cursor and Esc drops the picks", () => {
    const { svg } = setup();
    svg.focus();
    const key = (k: string, times = 1, altKey = false) => {
      for (let i = 0; i < times; i += 1) fireEvent.keyDown(svg, { key: k, altKey });
    };
    // The cursor starts at the view centre (2.5, 1); the grid step is 0.1 m, Alt moves 10 steps.
    key("ArrowUp", 1, true);
    key("ArrowLeft", 5);
    key("Enter");
    key("ArrowDown", 2, true);
    key("ArrowLeft", 2, true);
    key("Enter");
    key("ArrowRight", 5, true);
    key("Enter");
    const chips = () => screen.getAllByTestId("constraint-chip").map((chip) => chip.textContent);
    expect(chips()).toEqual([
      expect.stringContaining("x 2,00 m · y 2,00 m"),
      expect.stringContaining("x 0,00 m · y 0,00 m"),
      expect.stringContaining("x 5,00 m · y 0,00 m"),
    ]);
    key("Escape");
    expect(chips().every((chip) => chip?.includes(CONSTRAINT_TEXT.empty))).toBe(true);
  });

  it("does not pick a target on a locked layer", () => {
    const { click } = setup({ lockedTarget: true });
    click(P);
    const target = screen.getAllByTestId("constraint-chip")[0];
    expect(target?.textContent).toContain(CONSTRAINT_TEXT.empty);
  });

  it("is in the palette with the G shortcut and disabled in read-only mode", () => {
    const { ui } = setup({ tool: "select" });
    fireEvent.keyDown(document, { key: "g" });
    expect(ui.getState().activeTool).toBe("triangulate");
    expect(screen.getByRole("complementary", { name: TRIANGULATION_TEXT.title })).toBeTruthy();
    cleanup();
    setup({ readOnly: true });
    const button = screen.getByRole("button", { name: "Triangulación" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.queryByRole("complementary", { name: TRIANGULATION_TEXT.title })).toBeNull();
  });
});

describe("baseline-offset dialog", () => {
  function baselineSetup(s: string, d: string, options: SetupOptions = {}) {
    const env = setup({ tool: "baseline", ...options });
    env.click(P);
    env.click(A);
    env.click(B);
    type(BASELINE_TEXT.along, s);
    type(BASELINE_TEXT.offset, d);
    return env;
  }

  it("previews P = A + s·û + d·n̂ and sends baseline_offset", async () => {
    const { calls } = baselineSetup("1,5", "0.8");
    expect(screen.getByTestId("constraint-baseline")).toBeTruthy();
    expect(candidates().map((m) => m.getAttribute("d"))).toEqual(["M1.5,-0.8h0"]);
    fireEvent.click(screen.getByRole("radio", { name: BASELINE_TEXT.right }));
    expect(candidates().map((m) => m.getAttribute("d"))).toEqual(["M1.5,0.8h0"]);
    await apply();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual(PATCH_THEN_RELOAD);
    expect(calls[0]?.body).toEqual({
      operation: "baseline_offset",
      base_a: A_ID,
      base_b: B_ID,
      s_m: 1.5,
      d_m: 0.8,
      side: "right",
    });
    expect(screen.getByText(BASELINE_TEXT.done).getAttribute("role")).toBe("status");
  });

  it("accepts a negative s and a zero offset", async () => {
    const { calls } = baselineSetup("-2", "0");
    await apply();
    expect(calls[0]?.body).toMatchObject({ s_m: -2, d_m: 0, side: "left" });
  });

  it("a negative offset is rejected locally", async () => {
    const { calls } = baselineSetup("1", "-0,5");
    await apply();
    expect(screen.getByRole("alert").textContent).toBe(BASELINE_TEXT.badOffset);
    expect(calls).toEqual([]);
  });

  it("a 422 V-07 renders the PRD message", async () => {
    baselineSetup("1", "1", { patch: json({ title: "x", code: "V-07" }, 422) });
    await apply();
    expect(screen.getByRole("alert").textContent).toBe(V07);
  });
});
