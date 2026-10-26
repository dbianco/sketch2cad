import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import { JOB_FAILURE_TEXT } from "../../features/jobs/JobFailure";
import { EditorWorkspace } from "../EditorWorkspace";
import type { Catalog } from "../canvas/EditorCanvas";
import type { Viewport, WorldRect } from "../canvas/viewport";
import catalogJson from "../fixtures/catalog-default-1.json";
import { createSnapSettingsStore } from "../panels/SnapSettings";
import { createPlanStore } from "../plan/planStore";
import { createEditorStore } from "../store/entities";
import type { EntityOut, Geometry, Point2 } from "../store/types";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";
import { DISTANCE_MESSAGE } from "../util/parseDistance";
import {
  OCR_POLL_INTERVAL_MS,
  OCR_REPLACED_REASON,
  OCR_TEXT,
  boxToSourcePx,
} from "./OcrCropTool";

type JobOut = components["schemas"]["JobOut"];
type OcrResult = components["schemas"]["OcrResultOut"];

const CATALOG = catalogJson as Catalog;
const PLAN_ID = "00000000-0000-4000-8000-0000000000a1";
const REVISION_ID = "00000000-0000-4000-8000-0000000000b1";
const SOURCE_ID = "00000000-0000-4000-8000-0000000000d1";
const USER_ID = "00000000-0000-4000-8000-0000000000e9";
const JOB_ID = "00000000-0000-4000-8000-0000000000f1";
const LINE_ID = "00000000-0000-4000-8000-0000000000e1";
const DIM_ID = "00000000-0000-4000-8000-0000000000e2";
const JOBS_URL = `/api/v1/plans/${PLAN_ID}/jobs`;
const JOB_URL = `/api/v1/jobs/${JOB_ID}`;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const ORIGIN = { left: 40, top: 30 };
/** 2000 x 1000 px image over 10 x 5 m: 200 px per metre, top row at y = 5. */
const BOUNDS: WorldRect = { minX: 0, minY: 0, maxX: 10, maxY: 5 };
const SOURCE = { id: SOURCE_ID, widthPx: 2000, heightPx: 1000 };
const IMAGE = { sourceId: SOURCE_ID, widthPx: 2000, heightPx: 1000, bounds: BOUNDS };

function entityOut(id: string, type: EntityOut["type"], layer: string, geometry: Geometry): EntityOut {
  return {
    id,
    type,
    layer,
    geometry,
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

const LINE = entityOut(LINE_ID, "line", "L_STRUCTURE", { kind: "line", coords_m: [[1, 1], [4, 1]] });
const DIMENSION = entityOut(DIM_ID, "dimension", "L_DIMENSIONS", {
  kind: "dimension",
  p1_m: [6, 1],
  p2_m: [8, 1],
  offset_m: 0.5,
  declared_value_m: null,
  measured_value_m: 2,
});

function result(overrides: Partial<OcrResult> = {}): OcrResult {
  return {
    text: "4.20",
    raw_text: "4,20 m",
    value_m: 4.2,
    unit_detected: "m",
    reason: null,
    confidence: 0.82,
    preselect: true,
    min_confidence: 0.6,
    model_version: "trocr-test",
    ...overrides,
  };
}

function job(overrides: Partial<JobOut> = {}): JobOut {
  return {
    id: JOB_ID,
    kind: "ocr_crop",
    status: "queued",
    error: null,
    result: null,
    result_ref: null,
    source_id: SOURCE_ID,
    ...overrides,
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

interface SetupOptions {
  aiAssist?: boolean;
  tool?: ToolId;
  lease?: boolean;
  /** Answer of `POST /plans/{id}/jobs`. */
  post?: () => Response;
  /** Answers of `GET /jobs/{id}`, one per poll; the last one repeats. */
  polls?: JobOut[];
  select?: string[];
}

function setup(options: SetupOptions = {}) {
  const calls: Call[] = [];
  const polls = options.polls ?? [job({ status: "succeeded", result: result() })];
  let poll = 0;
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const url = String(input);
    const call: Call = {
      method: init?.method ?? "GET",
      url,
      body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
    };
    calls.push(call);
    const path = new URL(url, "http://localhost").pathname;
    if (call.method === "POST" && path === JOBS_URL) return Promise.resolve(options.post?.() ?? json(job(), 202));
    if (call.method === "GET" && path === JOB_URL) {
      const answer = polls[Math.min(poll, polls.length - 1)];
      poll += 1;
      return Promise.resolve(json(answer));
    }
    if (call.method === "POST" && path === ENTITIES_URL) return Promise.resolve(json({ entities: [DIMENSION] }, 201));
    if (call.method === "GET" && path === ENTITIES_URL) return Promise.resolve(json({ entities: [LINE] }));
    if (call.method === "DELETE") return Promise.resolve(json(DIMENSION));
    return Promise.resolve(json({ title: "Not Found" }, 404));
  });
  const api = createApiClient({ fetch: fetchMock });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, [LINE, DIMENSION]);
  if (options.select !== undefined) store.getState().select(options.select);
  const ui = createEditorUiStore({ activeTool: options.tool ?? "ocr" });
  const plan = createPlanStore({
    planId: PLAN_ID,
    revisionId: REVISION_ID,
    userId: USER_ID,
    lease:
      options.lease === false
        ? null
        : { user_id: USER_ID, display_name: "technician", expires_at: "2999-01-01T00:00:00Z" },
  });
  let viewport: Viewport | null = null;
  const view = render(
    <EditorWorkspace
      catalog={CATALOG}
      store={store}
      ui={ui}
      plan={plan}
      api={api}
      aiAssist={options.aiAssist ?? true}
      source={SOURCE}
      imageUrl="/preview.png"
      imageBounds={BOUNDS}
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
  const client = (p: Point2) => {
    if (viewport === null) throw new Error("No viewport yet");
    const { centerX, centerY, scale, width, height } = viewport;
    return {
      pointerId: 1,
      button: 0,
      clientX: (p[0] - centerX) * scale + width / 2 + ORIGIN.left,
      clientY: (centerY - p[1]) * scale + height / 2 + ORIGIN.top,
    };
  };
  /** Drag a box between two world points. */
  const drag = (a: Point2, b: Point2) => {
    fireEvent.pointerDown(svg, client(a));
    fireEvent.pointerMove(svg, client(b));
    fireEvent.pointerUp(svg, client(b));
  };
  const click = (p: Point2) => {
    fireEvent.pointerDown(svg, client(p));
    fireEvent.pointerUp(svg, client(p));
  };
  const sent = (method: string, url: string) =>
    calls.filter((call) => call.method === method && new URL(call.url, "http://localhost").pathname === url);
  return { calls, sent, store, ui, view, drag, click };
}

/** Let the POST settle and run `n` polls. */
async function runPolls(n = 1): Promise<void> {
  await act(() => vi.advanceTimersByTimeAsync(0));
  for (let i = 0; i < n; i += 1) await act(() => vi.advanceTimersByTimeAsync(OCR_POLL_INTERVAL_MS));
}

/** Drag the standard box, (1, 4)-(2, 3.5) = px [200, 200, 400, 300], and wait for the reading. */
async function readBox(ctx: ReturnType<typeof setup>, polls = 1): Promise<void> {
  ctx.drag([1, 4], [2, 3.5]);
  await runPolls(polls);
}

function field(): HTMLInputElement {
  return screen.getByRole("textbox", { name: OCR_TEXT.field });
}

function panel(): HTMLElement {
  return screen.getByRole("complementary", { name: OCR_TEXT.title });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("boxToSourcePx", () => {
  it("maps world corners to original pixels, top row at maxY, rounding outwards", () => {
    expect(boxToSourcePx([2, 3.5], [1, 4], IMAGE)).toEqual([200, 200, 400, 300]);
    expect(boxToSourcePx([1.0001, 4], [2.0001, 3.4999], IMAGE)).toEqual([200, 200, 401, 301]);
  });

  it("clips to the image and rejects boxes outside it, too small or too large", () => {
    expect(boxToSourcePx([-1, 6], [0.5, 4.5], IMAGE)).toEqual([0, 0, 100, 100]);
    expect(boxToSourcePx([11, 1], [12, 2], IMAGE)).toBe(OCR_TEXT.outside);
    expect(boxToSourcePx([1, 1], [1.02, 2], IMAGE)).toBe(OCR_TEXT.tooSmall);
    expect(boxToSourcePx([0, 0], [10.5, 1], IMAGE)).toEqual([0, 800, 2000, 1000]);
    expect(boxToSourcePx([0, 0], [10, 1], { ...IMAGE, widthPx: 4000 })).toBe(OCR_TEXT.tooLarge);
  });
});

describe("OcrCropTool", () => {
  it("is not offered and does nothing while AI assistance is off", async () => {
    const ctx = setup({ aiAssist: false });
    expect(screen.queryByRole("button", { name: "Leer cota (OCR)" })).toBeNull();
    expect(screen.queryByRole("complementary", { name: OCR_TEXT.title })).toBeNull();
    ctx.drag([1, 4], [2, 3.5]);
    await runPolls();
    expect(ctx.sent("POST", JOBS_URL)).toHaveLength(0);
  });

  it("is offered in the palette with its shortcut while AI assistance is on", () => {
    const ctx = setup({ tool: "select" });
    const button = screen.getByRole("button", { name: "Leer cota (OCR)" });
    expect(button.getAttribute("aria-keyshortcuts")).toBe("R");
    fireEvent.keyDown(document.body, { key: "r" });
    expect(ctx.ui.getState().activeTool).toBe("ocr");
  });

  it("sends the box in original pixels of the primary source and polls the job", async () => {
    const ctx = setup({ polls: [job({ status: "running" })] });
    ctx.drag([1, 4], [2, 3.5]);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(ctx.sent("POST", JOBS_URL).map((call) => call.body)).toEqual([
      { kind: "ocr_crop", params: { source_id: SOURCE_ID, box_px: [200, 200, 400, 300] } },
    ]);
    expect(within(panel()).getByRole("status").textContent).toBe(OCR_TEXT.reading);
    await act(() => vi.advanceTimersByTimeAsync(OCR_POLL_INTERVAL_MS * 2));
    expect(ctx.sent("GET", JOB_URL)).toHaveLength(2);
  });

  it("preselects and focuses the value of a reading with confidence 0.82", async () => {
    const ctx = setup();
    await readBox(ctx);
    expect(field().value).toBe("4,2");
    expect(document.activeElement).toBe(field());
    expect(panel().textContent).toContain("Confianza: 0,82 (mínima para proponer: 0,60)");
    expect(panel().textContent).toContain("Valor propuesto: 4,20 m");
  });

  it("preselects nothing below 0.6 and shows the raw text as a hint", async () => {
    const ctx = setup({
      polls: [job({ status: "succeeded", result: result({ confidence: 0.45, preselect: false }) })],
    });
    await readBox(ctx);
    expect(field().value).toBe("");
    expect(document.activeElement).not.toBe(field());
    const hint = document.getElementById(field().getAttribute("aria-describedby") ?? "");
    expect(hint?.textContent).toBe(OCR_TEXT.rawHint("4,20 m"));
  });

  it("follows the server's preselect at the 0.6 boundary", async () => {
    const at = setup({
      polls: [job({ status: "succeeded", result: result({ confidence: 0.6, preselect: true }) })],
    });
    await readBox(at);
    expect(field().value).toBe("4,2");
    cleanup();
    const refused = setup({
      polls: [job({ status: "succeeded", result: result({ confidence: 0.6, preselect: false }) })],
    });
    await readBox(refused);
    expect(field().value).toBe("");
  });

  it("shows the cause and action of a failed job and leaves the editor enabled", async () => {
    const error = { cause: "La imagen del recorte es ilegible.", action: "Marca un recuadro más amplio." };
    const ctx = setup({ polls: [job({ status: "failed", error })] });
    await readBox(ctx);
    const alert = within(panel()).getByRole("alert");
    expect(alert.textContent).toContain(`${JOB_FAILURE_TEXT.cause} ${error.cause}`);
    expect(alert.textContent).toContain(`${JOB_FAILURE_TEXT.action} ${error.action}`);
    const toolbar = screen.getByRole("group", { name: "Herramientas" });
    const buttons = within(toolbar).getAllByRole("button") as HTMLButtonElement[];
    expect(buttons.every((button) => !button.disabled)).toBe(true);
    expect((screen.getByRole("combobox", { name: "Capa activa" }) as HTMLSelectElement).disabled).toBe(false);
    // A new box can be read right away, and the dismiss button clears the failure.
    fireEvent.click(within(alert).getByRole("button", { name: JOB_FAILURE_TEXT.dismiss }));
    expect(within(panel()).queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Línea" }));
    expect(ctx.ui.getState().activeTool).toBe("line");
  });

  it("explains a 422 of the job request and a box too small to send", async () => {
    const ctx = setup({ post: () => json({ title: "Unprocessable", code: "ocr_box_invalid" }, 422) });
    await readBox(ctx);
    expect(within(panel()).getByRole("alert").textContent).toBe(OCR_TEXT.boxInvalid);
    expect(ctx.sent("GET", JOB_URL)).toHaveLength(0);
    ctx.drag([1, 4], [1.01, 3.99]);
    await runPolls();
    expect(within(panel()).getByRole("alert").textContent).toBe(OCR_TEXT.tooSmall);
    expect(ctx.sent("POST", JOBS_URL)).toHaveLength(1);
  });

  it("accepts the reading as a dimension over the selected line with ocr_job_id", async () => {
    const ctx = setup({ select: [LINE_ID] });
    await readBox(ctx);
    expect(panel().textContent).toContain(OCR_TEXT.target(OCR_TEXT.segment(1, 3)));
    fireEvent.click(screen.getByRole("button", { name: OCR_TEXT.accept }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(ctx.sent("POST", ENTITIES_URL).map((call) => call.body)).toEqual([
      {
        entities: [
          {
            type: "dimension",
            layer: "L_DIMENSIONS",
            attributes: {},
            geometry: { kind: "dimension", p1_m: [1, 1], p2_m: [4, 1], offset_m: 0, declared_value_m: 4.2 },
            ocr_job_id: JOB_ID,
          },
        ],
      },
    ]);
    expect(within(panel()).getAllByRole("status").map((p) => p.textContent)).toContain(
      OCR_TEXT.done(4.2, false),
    );
  });

  it("takes a typed value, checked first, for a segment clicked on the canvas", async () => {
    const ctx = setup({
      polls: [job({ status: "succeeded", result: result({ confidence: 0.3, preselect: false }) })],
    });
    await readBox(ctx);
    expect(panel().textContent).toContain(OCR_TEXT.pickTarget);
    ctx.click([2.5, 1]);
    expect(panel().textContent).toContain(OCR_TEXT.target(OCR_TEXT.segment(1, 3)));
    fireEvent.change(field(), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: OCR_TEXT.accept }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(within(panel()).getByRole("alert").textContent).toBe(DISTANCE_MESSAGE);
    fireEvent.change(field(), { target: { value: "4.35" } });
    fireEvent.click(screen.getByRole("button", { name: OCR_TEXT.accept }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    const posted = ctx.sent("POST", ENTITIES_URL).map((call) => call.body) as {
      entities: { geometry: { declared_value_m: number }; ocr_job_id: string }[];
    }[];
    expect(posted).toHaveLength(1);
    expect(posted[0]?.entities[0]?.geometry.declared_value_m).toBe(4.35);
    expect(posted[0]?.entities[0]?.ocr_job_id).toBe(JOB_ID);
    expect(panel().textContent).toContain(OCR_TEXT.done(4.35, true));
  });

  it("replaces a selected dimension, keeping its layer and offset", async () => {
    const ctx = setup({ select: [DIM_ID] });
    await readBox(ctx);
    fireEvent.click(screen.getByRole("button", { name: OCR_TEXT.accept }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    const body = ctx.sent("POST", ENTITIES_URL)[0]?.body as {
      entities: { layer: string; geometry: unknown }[];
    };
    expect(body.entities[0]?.layer).toBe("L_DIMENSIONS");
    expect(body.entities[0]?.geometry).toEqual({
      kind: "dimension",
      p1_m: [6, 1],
      p2_m: [8, 1],
      offset_m: 0.5,
      declared_value_m: 4.2,
    });
    const deletes = ctx.sent("DELETE", `/api/v1/entities/${DIM_ID}`);
    expect(deletes.map((call) => new URL(call.url, "http://localhost").searchParams.get("reason"))).toEqual([
      OCR_REPLACED_REASON,
    ]);
  });

  it("reads without the lease but needs it to create the dimension", async () => {
    const ctx = setup({ lease: false, select: [LINE_ID] });
    await readBox(ctx);
    expect(ctx.sent("POST", JOBS_URL)).toHaveLength(1);
    const accept = screen.getByRole("button", { name: OCR_TEXT.accept }) as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
    expect(document.getElementById(accept.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
      OCR_TEXT.needsLease,
    );
  });

  it("stops polling when the editor unmounts or the tool changes", async () => {
    const running = [job({ status: "running" })];
    const ctx = setup({ polls: running });
    await readBox(ctx);
    expect(ctx.sent("GET", JOB_URL)).toHaveLength(1);
    ctx.view.unmount();
    await act(() => vi.advanceTimersByTimeAsync(OCR_POLL_INTERVAL_MS * 3));
    expect(ctx.sent("GET", JOB_URL)).toHaveLength(1);

    const other = setup({ polls: running });
    await readBox(other);
    expect(other.sent("GET", JOB_URL)).toHaveLength(1);
    act(() => other.ui.getState().setTool("select"));
    await act(() => vi.advanceTimersByTimeAsync(OCR_POLL_INTERVAL_MS * 3));
    expect(other.sent("GET", JOB_URL)).toHaveLength(1);
  });
});
