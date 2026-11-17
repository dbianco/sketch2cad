import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type APIResponse, type Page } from "@playwright/test";
import { rejectChainedSuggestions } from "./support";

// Every editor tool against the compose stack (jotdown H2-11, CU-04): line, polyline, point,
// text, symbol, vertex move, layer reclassify, delete, undo and redo, drawn with the real pointer
// and keyboard. After each mutation the test waits for "Sincronizado", then checks the revision's
// live entities (GET /revisions/{id}/entities) and that the plan's event log
// (GET /case-files/{id}/events?plan_id=, every page) gained exactly ONE event of the expected
// kind. The seed (case, calibration, initial entities) and the drawing script live in
// fixtures/sample_plan.json, in world metres; clicks are placed with the canvas's screen CTM.
//
// The editor has no measure tool ("medir" of CU-04): nothing is asserted for it.

const IMAGE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
const PLAN = JSON.parse(
  readFileSync(path.join(import.meta.dirname, "fixtures", "sample_plan.json"), "utf8"),
) as SamplePlan;
// Generous bound for the worker to pick the QC job up on a cold stack.
const QC_TIMEOUT_MS = 60_000;
/** Debounced sync (800 ms) plus the request round trips. */
const SYNC_TIMEOUT_MS = 15_000;
/** Allowed distance, in screen px, between a click and the vertex the API stored. */
const CLICK_TOLERANCE_PX = 2;
/** Steps of the pointer moves of a drag, so that the canvas sees intermediate positions. */
const DRAG_STEPS = 8;

type Point2 = [number, number];

interface EntityIn {
  type: string;
  layer: string;
  geometry: Record<string, unknown>;
  attributes: Record<string, unknown>;
}

interface SamplePlan {
  case: Record<string, string>;
  plan: { title: string; source_mode: string };
  calibration: Record<string, unknown>;
  seed_entities: EntityIn[];
  steps: {
    line: { from: Point2; to: Point2 };
    polyline: { vertices: Point2[] };
    point: { at: Point2 };
    text: { at: Point2; content: string; layer: string };
    symbol: {
      symbol_id: string;
      label: string;
      layer: string;
      evidence_number: number;
      at: Point2;
    };
    move_vertex: { pick: Point2; index: number; to: Point2 };
    reclassify: { pick: Point2; layer: string };
    delete: { pick: Point2 };
  };
}

interface EntityOut {
  id: string;
  type: string;
  layer: string;
  geometry: {
    kind: string;
    coords_m?: Point2[];
    closed?: boolean;
    anchor_m?: Point2;
    content?: string;
    symbol_id?: string;
    attributes?: Record<string, unknown>;
  };
  deleted_at: string | null;
}

interface EventOut {
  id: number;
  kind: string;
  plan_id: string | null;
  revision_id: string | null;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
}

interface Seeded {
  caseId: string;
  planId: string;
  revisionId: string;
  seedIds: string[];
}

/** Credentials of the user created by scripts/e2e-stack.sh (username and password lines). */
function readCredentials(): { username: string; password: string } {
  const file = process.env.E2E_CREDENTIALS_FILE;
  if (file === undefined) {
    throw new Error("E2E_CREDENTIALS_FILE is not set; run the suite through playwright.config.ts");
  }
  const [username = "", password = ""] = readFileSync(file, "utf8").split("\n");
  if (username === "" || password === "") {
    throw new Error("the E2E credentials file is incomplete");
  }
  return { username, password };
}

async function logIn(page: Page): Promise<void> {
  const { username, password } = readCredentials();
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(username);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Expedientes" })).toBeVisible();
}

async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((c) => c.name === "s2c_csrf");
  if (cookie === undefined) throw new Error("no s2c_csrf cookie after login");
  return { "X-CSRF-Token": cookie.value };
}

async function json<T>(response: APIResponse, status: number): Promise<T> {
  expect(response.status(), await response.text()).toBe(status);
  return (await response.json()) as T;
}

/** Case, uploaded image (QC finished), plan, accepted calibration (EDITING) and seed entities. */
async function seed(page: Page): Promise<Seeded> {
  const token = Date.now().toString(36);
  const headers = await csrfHeaders(page);
  const caseFile = await json<{ id: string }>(
    await page.request.post("/api/v1/case-files", {
      headers,
      data: { ...PLAN.case, description: `${PLAN.case.description ?? ""} ${token}` },
    }),
    201,
  );
  const bytes = readFileSync(IMAGE);
  const source = await json<{ id: string; qc_job_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/sources`, {
      headers,
      multipart: {
        file: { name: path.basename(IMAGE), mimeType: "image/png", buffer: bytes },
        client_sha256: createHash("sha256").update(bytes).digest("hex"),
      },
    }),
    201,
  );
  await expect
    .poll(
      async () =>
        (
          await json<{ status: string }>(
            await page.request.get(`/api/v1/jobs/${source.qc_job_id}`),
            200,
          )
        ).status,
      { timeout: QC_TIMEOUT_MS },
    )
    .toBe("succeeded");
  const plan = await json<{ id: string; current_revision_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/plans`, {
      headers,
      data: { ...PLAN.plan, title: `${PLAN.plan.title} ${token}`, primary_source_id: source.id },
    }),
    201,
  );
  await json(await page.request.post(`/api/v1/plans/${plan.id}/lease`, { headers }), 200);
  const calibrated = await json<{ status: string }>(
    await page.request.post(`/api/v1/plans/${plan.id}/calibration/accept`, {
      headers,
      data: PLAN.calibration,
    }),
    200,
  );
  expect(calibrated.status).toBe("EDITING");
  // Not about AI assistance: drop the suggestions chained after the calibration.
  await rejectChainedSuggestions(page, plan.current_revision_id);
  const created = await json<{ entities: EntityOut[] }>(
    await page.request.post(`/api/v1/revisions/${plan.current_revision_id}/entities`, {
      headers,
      data: { entities: PLAN.seed_entities },
    }),
    201,
  );
  return {
    caseId: caseFile.id,
    planId: plan.id,
    revisionId: plan.current_revision_id,
    seedIds: created.entities.map((entity) => entity.id),
  };
}

/** Live (not deleted) entities of the revision. */
async function liveEntities(page: Page, revisionId: string): Promise<EntityOut[]> {
  const body = await json<{ entities: EntityOut[] }>(
    await page.request.get(`/api/v1/revisions/${revisionId}/entities`),
    200,
  );
  return body.entities.filter((entity) => entity.deleted_at === null);
}

/** Every event of the plan, following `next_cursor` through all pages. */
async function planEvents(page: Page, seeded: Seeded): Promise<EventOut[]> {
  const events: EventOut[] = [];
  let cursor: string | null = null;
  do {
    const params: Record<string, string> = { plan_id: seeded.planId };
    if (cursor !== null) params.cursor = cursor;
    const body: { items: EventOut[]; next_cursor: string | null } = await json(
      await page.request.get(`/api/v1/case-files/${seeded.caseId}/events`, { params }),
      200,
    );
    events.push(...body.items);
    cursor = body.next_cursor;
  } while (cursor !== null);
  return events;
}

/** The part of an SVG matrix the test uses (the e2e tsconfig has no DOM lib). */
interface ScreenMatrix {
  a: number;
  b: number;
  c: number;
  d: number;
  e: number;
  f: number;
}

interface Rect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** The svg canvas as the test reads it (the e2e tsconfig has no DOM lib). */
interface SvgCanvas {
  getScreenCTM(): ScreenMatrix | null;
  getAttribute(name: string): string | null;
  parentElement: { clientWidth: number; clientHeight: number; getBoundingClientRect(): Rect };
}

/** Screen px per world metre at every click; the tolerance uses the coarsest. */
const clickScales: number[] = [];

/** Allowed distance, in metres, between a click and the vertex the API stored. */
function clickTolerance(): number {
  return CLICK_TOLERANCE_PX / Math.min(...clickScales);
}

interface CanvasLayout {
  /** The svg's size attributes match its container (the resize has been rendered). */
  settled: boolean;
  /** Screen px per world metre. */
  scale: number;
  /** Client position of the world point, through the canvas's screen CTM. */
  x: number;
  y: number;
  /** The point is inside the visible canvas. */
  inside: boolean;
}

/**
 * Client (viewport) position of a world point. The canvas follows its container's size one
 * render late (ResizeObserver), so the position is only read once the svg matches its container:
 * a side panel that opens or closes changes the canvas width.
 */
async function toClient(page: Page, world: Point2): Promise<{ x: number; y: number }> {
  const canvas = page.getByTestId("editor-canvas");
  const read = () =>
    canvas.evaluate((svg, [x, y]): CanvasLayout => {
      const element = svg as unknown as SvgCanvas;
      const m = element.getScreenCTM();
      if (m === null) throw new Error("the canvas has no screen CTM");
      const container = element.parentElement;
      const box = container.getBoundingClientRect();
      const width = Number(element.getAttribute("width"));
      const height = Number(element.getAttribute("height"));
      // The canvas draws world (x, y) at SVG (x, -y).
      const point = { x: m.a * x - m.c * y + m.e, y: m.b * x - m.d * y + m.f };
      return {
        settled:
          Math.abs(width - container.clientWidth) < 1 &&
          Math.abs(height - container.clientHeight) < 1,
        scale: m.a,
        ...point,
        inside:
          point.x > box.left && point.x < box.right && point.y > box.top && point.y < box.bottom,
      };
    }, world);
  await expect
    .poll(async () => (await read()).settled, { message: "canvas size settled" })
    .toBe(true);
  const layout = await read();
  expect(layout.inside, `world point ${JSON.stringify(world)} is on the visible canvas`).toBe(
    true,
  );
  clickScales.push(layout.scale);
  return { x: layout.x, y: layout.y };
}

async function clickAt(page: Page, world: Point2): Promise<void> {
  const { x, y } = await toClient(page, world);
  await page.mouse.click(x, y);
}

/** `actual` is within the click tolerance of `expected`. */
function expectNear(actual: readonly number[] | undefined, expected: Point2): void {
  const [x = Number.NaN, y = Number.NaN] = actual ?? [];
  const message = `${JSON.stringify(actual)} near ${JSON.stringify(expected)}`;
  expect(Math.hypot(x - expected[0], y - expected[1]), message).toBeLessThanOrEqual(
    clickTolerance(),
  );
}

/**
 * Tracks the plan's event log and the live entities: each `expectOne` waits for the sync, then
 * asserts that exactly one event was added since the previous check and returns it.
 */
class Ledger {
  private seen = 0;
  /** Live entity id -> [type, layer] as the test expects them. */
  readonly live = new Map<string, [string, string]>();

  constructor(
    private readonly page: Page,
    private readonly seeded: Seeded,
  ) {}

  async baseline(): Promise<EventOut[]> {
    const events = await planEvents(this.page, this.seeded);
    this.seen = events.length;
    return events;
  }

  async expectOne(kind: string): Promise<{ event: EventOut; entities: EntityOut[] }> {
    await expect
      .poll(async () => (await planEvents(this.page, this.seeded)).length, {
        timeout: SYNC_TIMEOUT_MS,
      })
      .toBeGreaterThan(this.seen);
    await expect(
      this.page.getByRole("status").filter({ hasText: /^Sincronizado$/ }),
    ).toBeVisible({ timeout: SYNC_TIMEOUT_MS });
    const events = await planEvents(this.page, this.seeded);
    const added = events.slice(this.seen);
    expect(
      added.map((event) => event.kind),
      "exactly one new event for the mutation",
    ).toEqual([kind]);
    this.seen = events.length;
    const event = added[0] as EventOut;
    expect(event.plan_id).toBe(this.seeded.planId);
    expect(event.revision_id).toBe(this.seeded.revisionId);
    const entities = await liveEntities(this.page, this.seeded.revisionId);
    return { event, entities };
  }

  /** The live entities are exactly the expected ones, with their type and layer. */
  checkLive(entities: EntityOut[]): void {
    const actual = Object.fromEntries(entities.map((e) => [e.id, [e.type, e.layer]]));
    expect(actual).toEqual(Object.fromEntries(this.live));
  }
}

function byId(entities: EntityOut[], id: string | null): EntityOut {
  const entity = entities.find((candidate) => candidate.id === id);
  expect(entity, `live entity ${String(id)}`).toBeDefined();
  return entity as EntityOut;
}

test.use({ viewport: { width: 1600, height: 1000 } });

test("every editor tool syncs its entity and writes one event per mutation", async ({ page }) => {
  test.setTimeout(180_000);
  const steps = PLAN.steps;
  await logIn(page);
  const seeded = await seed(page);
  const ledger = new Ledger(page, seeded);
  for (const [index, id] of seeded.seedIds.entries()) {
    const entity = PLAN.seed_entities[index] as EntityIn;
    ledger.live.set(id, [entity.type, entity.layer]);
  }

  await page.goto(`/cases/${seeded.caseId}/plans/${seeded.planId}/editor`);
  await expect(page.getByTestId("editor-canvas")).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeAttached();
  await expect(page.locator(`[data-entity-id="${seeded.seedIds[0] ?? ""}"]`)).toBeAttached();
  await expect(page.getByRole("status").filter({ hasText: /^Sincronizado$/ })).toBeVisible();
  const baseline = await ledger.baseline();
  expect(baseline.filter((event) => event.kind === "entity.created")).toHaveLength(
    PLAN.seed_entities.length,
  );
  const tool = (name: string) => page.getByRole("button", { name, exact: true });

  let lineId = "";
  await test.step("line: two clicks", async () => {
    await page.keyboard.press("l");
    await expect(tool("Línea")).toHaveAttribute("aria-pressed", "true");
    await clickAt(page, steps.line.from);
    await clickAt(page, steps.line.to);
    const { event, entities } = await ledger.expectOne("entity.created");
    const line = byId(entities, event.entity_id);
    lineId = line.id;
    ledger.live.set(line.id, ["line", "L_STRUCTURE"]);
    ledger.checkLive(entities);
    expect(line.geometry.kind).toBe("line");
    expectNear(line.geometry.coords_m?.[0], steps.line.from);
    expectNear(line.geometry.coords_m?.[1], steps.line.to);
  });

  let polylineId = "";
  let polylineGeometry: EntityOut["geometry"] | undefined;
  await test.step("polyline: clicks, finished with a double-click", async () => {
    await tool("Polilínea").click();
    await expect(tool("Polilínea")).toHaveAttribute("aria-pressed", "true");
    const vertices = steps.polyline.vertices;
    for (const vertex of vertices.slice(0, -1)) await clickAt(page, vertex);
    const last = await toClient(page, vertices.at(-1) as Point2);
    await page.mouse.dblclick(last.x, last.y);
    const { event, entities } = await ledger.expectOne("entity.created");
    const polyline = byId(entities, event.entity_id);
    polylineId = polyline.id;
    polylineGeometry = polyline.geometry;
    ledger.live.set(polyline.id, ["polyline", "L_STRUCTURE"]);
    ledger.checkLive(entities);
    expect(polyline.geometry.closed).toBe(false);
    expect(polyline.geometry.coords_m).toHaveLength(vertices.length);
    vertices.forEach((vertex, i) => expectNear(polyline.geometry.coords_m?.[i], vertex));
  });

  await test.step("point: one click", async () => {
    await page.keyboard.press("o");
    await expect(tool("Punto")).toHaveAttribute("aria-pressed", "true");
    await clickAt(page, steps.point.at);
    const { event, entities } = await ledger.expectOne("entity.created");
    const point = byId(entities, event.entity_id);
    ledger.live.set(point.id, ["point", "L_STRUCTURE"]);
    ledger.checkLive(entities);
    expectNear(point.geometry.coords_m?.[0], steps.point.at);
  });

  await test.step("text: click, type the content, Enter (on the active layer)", async () => {
    await page.getByRole("combobox", { name: "Capa activa" }).selectOption(steps.text.layer);
    await tool("Texto").click();
    await clickAt(page, steps.text.at);
    const input = page.getByRole("textbox", { name: "Contenido del texto" });
    await expect(input).toBeFocused();
    await page.keyboard.type(steps.text.content);
    await page.keyboard.press("Enter");
    await expect(input).toHaveCount(0);
    const { event, entities } = await ledger.expectOne("entity.created");
    const text = byId(entities, event.entity_id);
    ledger.live.set(text.id, ["text", steps.text.layer]);
    ledger.checkLive(entities);
    expect(text.geometry.content).toBe(steps.text.content);
    expectNear(text.geometry.anchor_m, steps.text.at);
  });

  await test.step("symbol: evidence marker with its evidence number", async () => {
    await tool("Símbolo").click();
    const picker = page.getByRole("complementary", { name: "Símbolos" });
    await picker.getByRole("button", { name: steps.symbol.label }).click();
    await picker
      .getByRole("textbox", { name: /Número de indicio/ })
      .fill(String(steps.symbol.evidence_number));
    await clickAt(page, steps.symbol.at);
    const { event, entities } = await ledger.expectOne("entity.created");
    const symbol = byId(entities, event.entity_id);
    ledger.live.set(symbol.id, ["symbol", steps.symbol.layer]);
    ledger.checkLive(entities);
    expect(symbol.geometry.symbol_id).toBe(steps.symbol.symbol_id);
    expect(symbol.geometry.attributes).toMatchObject({
      evidence_number: steps.symbol.evidence_number,
    });
    expectNear(symbol.geometry.anchor_m, steps.symbol.at);
  });

  await test.step("vertex move: drag the line's end handle", async () => {
    await page.keyboard.press("e");
    await expect(tool("Editar vértices")).toHaveAttribute("aria-pressed", "true");
    await clickAt(page, steps.move_vertex.pick);
    const handles = page.getByRole("group", { name: "Vértices de la entidad seleccionada" });
    await expect(handles.getByRole("button", { name: "Vértice 2 de 2" })).toBeVisible();
    const from = await toClient(page, steps.line.to);
    const to = await toClient(page, steps.move_vertex.to);
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: DRAG_STEPS });
    await page.mouse.up();
    const { event, entities } = await ledger.expectOne("entity.updated");
    expect(event.entity_id).toBe(lineId);
    expect(event.after?.operation).toBe("move_vertex");
    ledger.checkLive(entities);
    const line = byId(entities, lineId);
    expectNear(line.geometry.coords_m?.[0], steps.line.from);
    expectNear(line.geometry.coords_m?.[steps.move_vertex.index], steps.move_vertex.to);
  });

  const seedId = seeded.seedIds[0] ?? "";
  await test.step("reclassify: select the seeded line and change its layer", async () => {
    await tool("Seleccionar").click();
    await clickAt(page, steps.reclassify.pick);
    const panel = page.getByRole("region", { name: "Selección" });
    await expect(panel.getByText("1 entidad seleccionada")).toBeVisible();
    await panel
      .getByRole("combobox", { name: "Capa de la selección" })
      .selectOption(steps.reclassify.layer);
    const { event, entities } = await ledger.expectOne("entity.updated");
    expect(event.entity_id).toBe(seedId);
    expect(event.after?.operation).toBe("reclassify");
    expect(event.before?.layer).toBe(PLAN.seed_entities[0]?.layer);
    expect(event.after?.layer).toBe(steps.reclassify.layer);
    ledger.live.set(seedId, ["line", steps.reclassify.layer]);
    ledger.checkLive(entities);
  });

  await test.step("delete: select the polyline and remove it", async () => {
    await clickAt(page, steps.delete.pick);
    const panel = page.getByRole("region", { name: "Selección" });
    await expect(panel.getByText("1 entidad seleccionada")).toBeVisible();
    await panel.getByRole("button", { name: "Eliminar selección" }).click();
    const { event, entities } = await ledger.expectOne("entity.deleted");
    expect(event.entity_id).toBe(polylineId);
    ledger.live.delete(polylineId);
    ledger.checkLive(entities);
  });

  let restoredId = "";
  await test.step("undo (Ctrl/Cmd+Z): the deleted polyline is created again", async () => {
    await page.keyboard.press("ControlOrMeta+z");
    // The API has no undelete: undoing a synced delete creates the entity again with a NEW id
    // (store design, follow-up of task 059); the deleted row stays deleted.
    const { event, entities } = await ledger.expectOne("entity.created");
    expect(event.entity_id).not.toBe(polylineId);
    const restored = byId(entities, event.entity_id);
    restoredId = restored.id;
    expect(restored.geometry).toEqual(polylineGeometry);
    ledger.live.set(restoredId, ["polyline", "L_STRUCTURE"]);
    ledger.checkLive(entities);
  });

  await test.step("redo (Rehacer button): the polyline is deleted again", async () => {
    await page.getByRole("button", { name: "Rehacer", exact: true }).click();
    const { event, entities } = await ledger.expectOne("entity.deleted");
    expect(event.entity_id).toBe(restoredId);
    ledger.live.delete(restoredId);
    ledger.checkLive(entities);
    await expect(page.getByRole("button", { name: "Rehacer", exact: true })).toBeDisabled();
  });
});
