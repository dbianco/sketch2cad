import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type APIResponse, type Locator, type Page } from "@playwright/test";
import { rejectChainedSuggestions } from "./support";

// Metric reconstruction against the compose stack (H3-10, US-08, US-09, REQ-06, REQ-07, REQ-08):
// the fix-length tool (D) declares the length of two walls of a schematic sketch, the
// triangulation tool (G) locates an evidence marker from two wall corners, the evidence table
// shows it, the metric status badge follows, and the baseline-offset tool (B) moves it again from
// the same two corners. The seed (case, plan, calibration, walls, marker) goes through the API;
// every metric step goes through the real UI, and its effect is checked with
// GET /revisions/{id}/entities, GET /plans/{id}/metric-status and the plan's event log (exactly one
// new event per action). Geometry and hand-calculated values: fixtures/schematic_plan.json.
//
// Wall corners are references by PoC decision D-15 (docs/poc-assumptions.md): a vertex of a line
// is metric only once the line's length is declared, so the same triangulation before fix-length
// is rejected with V-07. A second test triangulates from reference points in a scaled drawing
// whose calibration was accepted as metric (hand-drawn points are "scaled_drawing" there).

const IMAGE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
const PLAN = JSON.parse(
  readFileSync(path.join(import.meta.dirname, "fixtures", "schematic_plan.json"), "utf8"),
) as SchematicPlan;
const QC_TIMEOUT_MS = 60_000;
const SYNC_TIMEOUT_MS = 15_000;
/** Acceptance criterion: the triangulated point within 1 cm of the hand-calculated one. */
const POINT_TOLERANCE_M = 0.01;
/** fix_length is exact up to floating point. */
const EXACT_M = 1e-9;
const V06 = "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas.";
const V07 = "Esta referencia no es métrica o depende del punto que intentas fijar.";
const NONE_REASON = "Sin origen métrico: colocada a mano sin cota";

type Point2 = [number, number];
type PlanKind = "schematic" | "scaled";

interface SchematicPlan {
  case: Record<string, string>;
  plans: Record<PlanKind, { plan: Record<string, string>; calibration: Record<string, unknown> }>;
  walls: { drawn: Point2[]; anchor_vertex: number; length: string; length_m: number; fixed: Point2[] }[];
  reference_layer: string;
  references: { A: Point2; B: Point2 };
  corners: {
    wall: number;
    A: { vertex: number; chip: string };
    B: { vertex: number; chip: string };
    undeclared_B: { at: Point2; chip: string };
  };
  marker: { at: Point2; drawn_x: string; drawn_y: string; evidence_number: number };
  evidence: string[];
  triangulation: {
    d_a: string;
    d_b: string;
    expected: Point2;
    expected_x: string;
    expected_y: string;
  };
  baseline: {
    s: string;
    d: string;
    expected: Point2;
    expected_x: string;
    expected_y: string;
  };
  rejection: { d_a: string; d_b: string };
}

type Ref = string | { entity_id: string; vertex: number };

interface EntityOut {
  id: string;
  type: string;
  layer: string;
  geometry: { kind: string; coords_m?: Point2[]; anchor_m?: Point2; attributes?: Record<string, unknown> };
  attributes: Record<string, unknown>;
  evidence_id: string | null;
  constraint: { kind: string; refs: Ref[]; params: Record<string, unknown> } | null;
  metric_origin: string;
  deleted_at: string | null;
}

interface EventOut {
  kind: string;
  entity_id: string | null;
  after: Record<string, unknown> | null;
}

interface MetricStatusOut {
  status: string;
  blocking_entities: string[];
  reasons: Record<string, string>;
}

interface Seeded {
  caseId: string;
  planId: string;
  revisionId: string;
  walls: string[];
  /** Reference points A and B (scaled drawing only). */
  points: string[];
  marker: string;
}

function readCredentials(): { username: string; password: string } {
  const file = process.env.E2E_CREDENTIALS_FILE;
  if (file === undefined) {
    throw new Error("E2E_CREDENTIALS_FILE is not set; run the suite through playwright.config.ts");
  }
  const [username = "", password = ""] = readFileSync(file, "utf8").split("\n");
  if (username === "" || password === "") throw new Error("the E2E credentials file is incomplete");
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

function line(coords: Point2[]) {
  return { type: "line", layer: "L_STRUCTURE", geometry: { kind: "line", coords_m: coords } };
}

function point(at: Point2) {
  return { type: "point", layer: PLAN.reference_layer, geometry: { kind: "point", coords_m: [at] } };
}

/**
 * Case, image (QC done), plan, lease, accepted calibration and the seed: the walls (schematic
 * sketch) or the reference points A and B (scaled drawing), and the evidence marker;
 * `evidenceLabel` creates evidence and links the marker to it.
 */
async function seed(page: Page, kind: PlanKind, evidenceLabel?: string): Promise<Seeded> {
  const token = Date.now().toString(36);
  const headers = await csrfHeaders(page);
  const { plan: planIn, calibration } = PLAN.plans[kind];
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
        (await json<{ status: string }>(await page.request.get(`/api/v1/jobs/${source.qc_job_id}`), 200))
          .status,
      { timeout: QC_TIMEOUT_MS },
    )
    .toBe("succeeded");
  const plan = await json<{ id: string; current_revision_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/plans`, {
      headers,
      data: { ...planIn, title: `${planIn.title ?? ""} ${token}`, primary_source_id: source.id },
    }),
    201,
  );
  await json(await page.request.post(`/api/v1/plans/${plan.id}/lease`, { headers }), 200);
  const accepted = await json<{ status: string }>(
    await page.request.post(`/api/v1/plans/${plan.id}/calibration/accept`, {
      headers,
      data: calibration,
    }),
    200,
  );
  expect(accepted.status).toBe("EDITING");
  // Not about AI assistance: drop the suggestions chained after the calibration.
  await rejectChainedSuggestions(page, plan.current_revision_id);
  let evidenceId: string | undefined;
  if (evidenceLabel !== undefined) {
    const evidence = await json<{ id: string }>(
      await page.request.post(`/api/v1/case-files/${caseFile.id}/evidence`, {
        headers,
        data: { label: evidenceLabel },
      }),
      201,
    );
    evidenceId = evidence.id;
  }
  const walls = kind === "schematic" ? PLAN.walls.map((wall) => line(wall.drawn)) : [];
  const marker = {
    type: "symbol",
    layer: "L_EVIDENCE",
    geometry: {
      kind: "symbol",
      anchor_m: PLAN.marker.at,
      symbol_id: "evidence_marker",
      attributes: { evidence_number: PLAN.marker.evidence_number },
    },
    ...(evidenceId === undefined ? {} : { evidence_id: evidenceId }),
  };
  const points =
    kind === "scaled" ? [point(PLAN.references.A), point(PLAN.references.B)] : [];
  const items = [...walls, ...points, marker];
  const created = await json<{ entities: EntityOut[] }>(
    await page.request.post(`/api/v1/revisions/${plan.current_revision_id}/entities`, {
      headers,
      data: { entities: items },
    }),
    201,
  );
  const ids = created.entities.map((entity) => entity.id);
  return {
    caseId: caseFile.id,
    planId: plan.id,
    revisionId: plan.current_revision_id,
    walls: ids.slice(0, walls.length),
    points: ids.slice(walls.length, walls.length + points.length),
    marker: ids.at(-1) ?? "",
  };
}

async function entity(page: Page, seeded: Seeded, id: string): Promise<EntityOut> {
  const body = await json<{ entities: EntityOut[] }>(
    await page.request.get(`/api/v1/revisions/${seeded.revisionId}/entities`),
    200,
  );
  const found = body.entities.find((candidate) => candidate.id === id && candidate.deleted_at === null);
  expect(found, `live entity ${id}`).toBeDefined();
  return found as EntityOut;
}

async function metricStatus(page: Page, seeded: Seeded): Promise<MetricStatusOut> {
  return json<MetricStatusOut>(await page.request.get(`/api/v1/plans/${seeded.planId}/metric-status`), 200);
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

/** The plan's event log: counts events before an action and checks what it added. */
class EventLog {
  private seen = 0;

  constructor(
    private readonly page: Page,
    private readonly seeded: Seeded,
  ) {}

  async baseline(): Promise<void> {
    this.seen = (await planEvents(this.page, this.seeded)).length;
  }

  /** Exactly one entity.updated for `entityId` with `operation` since the last check. */
  async expectOneUpdate(entityId: string, operation: string): Promise<void> {
    await expect
      .poll(async () => (await planEvents(this.page, this.seeded)).length, { timeout: SYNC_TIMEOUT_MS })
      .toBeGreaterThan(this.seen);
    await expect(this.page.getByRole("status").filter({ hasText: /^Sincronizado$/ })).toBeVisible({
      timeout: SYNC_TIMEOUT_MS,
    });
    const events = await planEvents(this.page, this.seeded);
    const added = events.slice(this.seen);
    this.seen = events.length;
    expect(
      added.map((event) => [event.kind, event.entity_id, event.after?.operation]),
      "exactly one new event for the action",
    ).toEqual([["entity.updated", entityId, operation]]);
  }

  async expectNone(): Promise<void> {
    expect((await planEvents(this.page, this.seeded)).slice(this.seen)).toEqual([]);
  }
}

interface SvgCanvas {
  getScreenCTM(): { a: number; b: number; c: number; d: number; e: number; f: number } | null;
  getAttribute(name: string): string | null;
  parentElement: { clientWidth: number; clientHeight: number };
}

/** Client position of a world point through the canvas's screen CTM, once its size settled. */
async function toClient(page: Page, world: Point2): Promise<{ x: number; y: number }> {
  const read = () =>
    page.getByTestId("editor-canvas").evaluate((svg, [x, y]) => {
      const element = svg as unknown as SvgCanvas;
      const m = element.getScreenCTM();
      if (m === null) throw new Error("the canvas has no screen CTM");
      const container = element.parentElement;
      return {
        settled:
          Math.abs(Number(element.getAttribute("width")) - container.clientWidth) < 1 &&
          Math.abs(Number(element.getAttribute("height")) - container.clientHeight) < 1,
        // The canvas draws world (x, y) at SVG (x, -y).
        x: m.a * x - m.c * y + m.e,
        y: m.b * x - m.d * y + m.f,
      };
    }, world);
  await expect.poll(async () => (await read()).settled, { message: "canvas size settled" }).toBe(true);
  const { x, y } = await read();
  return { x, y };
}

async function clickAt(page: Page, world: Point2): Promise<void> {
  const { x, y } = await toClient(page, world);
  await page.mouse.click(x, y);
}

/**
 * Click the painted outline of a symbol: the select tool hits only painted strokes (1,5 px),
 * and the evidence marker is an unfilled circle, so its centre selects nothing.
 */
async function clickOutline(page: Page, id: string): Promise<void> {
  const box = await page.locator(`[data-entity-id="${id}"]`).boundingBox();
  if (box === null) throw new Error(`entity ${id} is not rendered`);
  await page.mouse.click(box.x + box.width - 1, box.y + box.height / 2);
}

function expectAt(actual: readonly number[] | undefined, expected: Point2, tolerance: number): void {
  const [x = Number.NaN, y = Number.NaN] = actual ?? [];
  const message = `${JSON.stringify(actual)} within ${String(tolerance)} m of ${JSON.stringify(expected)}`;
  expect(Math.hypot(x - expected[0], y - expected[1]), message).toBeLessThanOrEqual(tolerance);
}

function badge(page: Page, label: string): Locator {
  return page.getByRole("button", { name: `Estado métrico: ${label}`, exact: true });
}

/** Open the metric panel, check the listed blockers by their text, close it. */
async function expectBlockers(page: Page, items: string[]): Promise<void> {
  await page.getByRole("button", { name: /^Estado métrico: / }).click();
  const panel = page.getByRole("region", { name: "Estado métrico del plano" });
  await expect(panel.getByRole("list").getByRole("button")).toHaveText(items);
  await page.getByRole("button", { name: /^Estado métrico: / }).click();
  await expect(panel).toHaveCount(0);
}

async function openEditor(page: Page, seeded: Seeded): Promise<void> {
  await page.goto(`/cases/${seeded.caseId}/plans/${seeded.planId}/editor`);
  await expect(page.getByTestId("editor-canvas")).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeAttached();
  await expect(page.locator(`[data-entity-id="${seeded.marker}"]`)).toBeAttached();
  await expect(page.getByRole("status").filter({ hasText: /^Sincronizado$/ })).toBeVisible();
}

/** Fix-length tool: pick the wall, its anchor, type the length, apply. */
async function fixLength(page: Page, wall: SchematicPlan["walls"][number]): Promise<void> {
  await page.keyboard.press("d");
  await expect(page.getByRole("button", { name: "Fijar longitud", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const [from, to] = wall.drawn as [Point2, Point2];
  await clickAt(page, [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2]);
  // The ends are buttons over the canvas inside a zero-size group (never "visible" as a box).
  await expect(page.getByRole("button", { name: "Extremo 1: elegir como ancla" })).toBeAttached();
  await clickAt(page, wall.drawn[wall.anchor_vertex] as Point2);
  const field = page.getByRole("textbox", { name: "Longitud del segmento (m)" });
  await expect(field).toBeFocused();
  await field.fill(wall.length);
  await page.getByRole("button", { name: "Aplicar", exact: true }).click();
  await expect(page.getByText(`Longitud fijada en ${wall.length} m`)).toBeVisible();
}

const TRIANGULATION = { key: "g", title: "Triangulación", slots: ["Referencia A", "Referencia B"] };
const BASELINE = { key: "b", title: "Base + offset", slots: ["Punto base A", "Punto base B"] };

/** A position to click and the chip text the pick must show. */
interface Pick {
  at: Point2;
  chip: string;
}

const MARKER_PICK: Pick = {
  at: PLAN.marker.at,
  chip: `Símbolo en x ${PLAN.marker.drawn_x} m · y ${PLAN.marker.drawn_y} m`,
};
const POINT_PICKS: [Pick, Pick] = [
  { at: PLAN.references.A, chip: "Punto en x 4,00 m · y -16,00 m" },
  { at: PLAN.references.B, chip: "Punto en x 10,00 m · y -16,00 m" },
];
const CORNER_PICKS: [Pick, Pick] = [
  { at: PLAN.references.A, chip: PLAN.corners.A.chip },
  { at: PLAN.references.B, chip: PLAN.corners.B.chip },
];

/**
 * References are clicked 3 px to the right of their position (within the 8 px vertex radius):
 * at the corner shared by the two walls the pointer is then on wall 1's stroke and not on wall
 * 2's, so the corner picked is wall 1's.
 */
const REFERENCE_NUDGE_PX = 3;

/** Triangulation (G) or baseline-offset (B) tool: pick the point to fix and A and B. */
async function pickConstraint(
  page: Page,
  tool: typeof TRIANGULATION,
  target: Pick,
  refs: [Pick, Pick],
): Promise<Locator> {
  await page.keyboard.press(tool.key);
  const panel = page.getByRole("complementary", { name: tool.title });
  await expect(panel).toBeVisible();
  await clickAt(page, target.at);
  for (const ref of refs) {
    const { x, y } = await toClient(page, ref.at);
    await page.mouse.click(x + REFERENCE_NUDGE_PX, y);
  }
  await expect(panel.getByTestId("constraint-chip")).toContainText([
    `Punto a fijar: ${target.chip}`,
    `${tool.slots[0] ?? ""}: ${refs[0].chip}`,
    `${tool.slots[1] ?? ""}: ${refs[1].chip}`,
  ]);
  return panel;
}

async function typeDistances(panel: Locator, dA: string, dB: string): Promise<void> {
  await panel.getByRole("textbox", { name: "Distancia a A (m)" }).fill(dA);
  await panel.getByRole("textbox", { name: "Distancia a B (m)" }).fill(dB);
}

/** Type the 3-4-5 distances, check the client preview, apply. */
async function applyTriangulation(panel: Locator): Promise<void> {
  const t = PLAN.triangulation;
  await typeDistances(panel, t.d_a, t.d_b);
  await expect(panel.getByText(`Posición: x ${t.expected_x} m · y ${t.expected_y} m`)).toBeVisible();
  await panel.getByRole("button", { name: "Aplicar", exact: true }).click();
}

async function openEvidenceTable(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Tabla de indicios", exact: true }).click();
  const table = page.getByRole("region", { name: "Tabla de indicios" });
  await expect(table).toBeVisible();
  return table;
}

/** Cells (label, X, Y, origin) of the row of evidence `number`. */
function evidenceCells(table: Locator, number: number): Locator {
  const selectButton = table
    .page()
    .getByRole("button", { name: `Seleccionar el indicio ${String(number)} en el plano` });
  return table.getByRole("row").filter({ has: selectButton }).getByRole("cell");
}

test.use({ viewport: { width: 1600, height: 1000 } });

const CORNER_HINT =
  "Las esquinas de una línea solo sirven como referencia si su longitud está fijada (herramienta Fijar longitud).";

test("schematic sketch: fix two walls, triangulate the evidence from their corners, plan Métrico", async ({
  page,
}) => {
  test.setTimeout(240_000);
  await logIn(page);
  const seeded = await seed(page, "schematic");
  const log = new EventLog(page, seeded);
  const wallId = seeded.walls[PLAN.corners.wall] ?? "";
  await openEditor(page, seeded);

  await test.step("badge Aproximado: the two hand-drawn walls block it", async () => {
    await expect(badge(page, "Aproximado")).toBeVisible();
    const status = await metricStatus(page, seeded);
    expect(status.status).toBe("approximate");
    // Listed by entity id, not by creation order.
    expect([...status.blocking_entities].sort()).toEqual([...seeded.walls].sort());
    expect(Object.values(status.reasons)).toEqual(["none", "none"]);
    await expectBlockers(page, [
      `Línea · L_STRUCTURE · ${NONE_REASON}`,
      `Línea · L_STRUCTURE · ${NONE_REASON}`,
    ]);
  });

  await test.step("triangulation (G) from the undeclared wall's corners: V-06, then V-07", async () => {
    const undeclared: [Pick, Pick] = [
      CORNER_PICKS[0],
      { at: PLAN.corners.undeclared_B.at, chip: PLAN.corners.undeclared_B.chip },
    ];
    const panel = await pickConstraint(page, TRIANGULATION, MARKER_PICK, undeclared);
    await log.baseline();
    await typeDistances(panel, PLAN.rejection.d_a, PLAN.rejection.d_b);
    await expect(panel.getByRole("alert")).toHaveText(V06);
    await expect(page.getByTestId("constraint-candidate")).toHaveCount(0);
    await panel.getByRole("button", { name: "Aplicar", exact: true }).click();
    await expect(panel.getByRole("alert")).toHaveText(V06);
    await log.expectNone();

    await typeDistances(panel, PLAN.triangulation.d_a, PLAN.triangulation.d_b);
    // The V-06 message of the previous attempt goes away with the new values.
    await expect(panel.getByRole("alert")).toHaveCount(0);
    await expect(page.getByTestId("constraint-candidate")).toHaveCount(2);
    await panel.getByRole("button", { name: "Aplicar", exact: true }).click();
    // The wall's length is not declared yet, so its corners are not metric (D-15).
    await expect(panel.getByRole("alert")).toHaveText(V07);
    await expect(panel.getByText(CORNER_HINT)).toBeVisible();
    await log.expectNone();
    const marker = await entity(page, seeded, seeded.marker);
    expect(marker.geometry.anchor_m).toEqual(PLAN.marker.at);
    expect(marker.constraint).toBeNull();
    expect(marker.metric_origin).toBe("none");
    expect((await entity(page, seeded, wallId)).metric_origin).toBe("none");
    await panel.getByRole("button", { name: "Cancelar", exact: true }).click();
    await expect(panel.getByTestId("constraint-chip")).toContainText([
      "Punto a fijar: sin elegir",
      "Referencia A: sin elegir",
      "Referencia B: sin elegir",
    ]);
  });

  for (const [index, wall] of PLAN.walls.entries()) {
    await test.step(`fix-length (D): wall ${String(index + 1)} = ${wall.length} m`, async () => {
      const id = seeded.walls[index] ?? "";
      await log.baseline();
      await fixLength(page, wall);
      await log.expectOneUpdate(id, "fix_length");
      const fixed = await entity(page, seeded, id);
      wall.fixed.forEach((vertex, i) => expectAt(fixed.geometry.coords_m?.[i], vertex, EXACT_M));
      expect(fixed.attributes.declared_segments).toEqual({ "0": wall.length_m });
      expect(fixed.metric_origin).toBe("declared_dimension");
    });
  }

  await test.step("both walls declared: Métrico (the marker places no evidence yet)", async () => {
    await expect(badge(page, "Métrico")).toBeVisible();
    expect((await metricStatus(page, seeded)).status).toBe("metric");
  });

  const table = await openEvidenceTable(page);
  await test.step("evidence table: register two items, assign the second to the marker", async () => {
    for (const label of PLAN.evidence) {
      await table.getByRole("textbox", { name: "Etiqueta del nuevo indicio" }).fill(label);
      await table.getByRole("button", { name: "Agregar indicio", exact: true }).click();
      await expect(table.getByRole("cell", { name: label, exact: true })).toBeVisible();
    }
    await page.getByRole("button", { name: "Seleccionar", exact: true }).click();
    await clickOutline(page, seeded.marker);
    await expect(page.getByText("1 entidad seleccionada")).toBeVisible();
    await log.baseline();
    await table.getByRole("button", { name: "Asignar al elemento seleccionado: 2" }).click();
    await log.expectOneUpdate(seeded.marker, "relabel");
    const marker = await entity(page, seeded, seeded.marker);
    const register = await json<{ items: { id: string; number: number }[] }>(
      await page.request.get(`/api/v1/case-files/${seeded.caseId}/evidence`),
      200,
    );
    expect(marker.evidence_id).toBe(register.items.find((item) => item.number === 2)?.id);
    // The marker's own number follows the register (it was seeded with 1).
    expect(marker.geometry.attributes).toEqual({ evidence_number: 2 });
    await expect(evidenceCells(table, 2)).toHaveText([
      PLAN.evidence[1] ?? "",
      PLAN.marker.drawn_x,
      PLAN.marker.drawn_y,
      "Sin origen métrico",
      /Editar etiqueta/,
    ]);
    await expect(table.getByRole("row").filter({ hasText: PLAN.evidence[0] ?? "" })).toContainText(
      "sin ubicar",
    );
  });

  await test.step("placed evidence without a metric origin: back to Aproximado", async () => {
    await expect(badge(page, "Aproximado")).toBeVisible();
    const status = await metricStatus(page, seeded);
    expect(status.blocking_entities).toEqual([seeded.marker]);
    expect(status.reasons).toEqual({ [seeded.marker]: "none" });
    await expectBlockers(page, [`Símbolo · L_EVIDENCE · ${NONE_REASON}`]);
  });

  await test.step("triangulation (G) from wall 1's corners: dA 3,60, dB 4,80", async () => {
    const panel = await pickConstraint(page, TRIANGULATION, MARKER_PICK, CORNER_PICKS);
    await log.baseline();
    await applyTriangulation(panel);
    await expect(panel.getByText("Punto fijado por triangulación.")).toBeVisible();
    await log.expectOneUpdate(seeded.marker, "triangulate");
    const marker = await entity(page, seeded, seeded.marker);
    expectAt(marker.geometry.anchor_m, PLAN.triangulation.expected, POINT_TOLERANCE_M);
    expect(marker.metric_origin).toBe("triangulation");
    expect(marker.constraint).toMatchObject({
      kind: "triangulation",
      refs: [
        { entity_id: wallId, vertex: PLAN.corners.A.vertex },
        { entity_id: wallId, vertex: PLAN.corners.B.vertex },
      ],
      params: { d_a_m: 3.6, d_b_m: 4.8 },
    });
  });

  await test.step("evidence table: number, label, coordinates with two decimals and a comma", async () => {
    await expect(evidenceCells(table, 2)).toHaveText([
      PLAN.evidence[1] ?? "",
      PLAN.triangulation.expected_x,
      PLAN.triangulation.expected_y,
      "Triangulación",
      /Editar etiqueta/,
    ]);
  });

  await test.step("metric status: Métrico", async () => {
    await expect(badge(page, "Métrico")).toBeVisible();
    const status = await metricStatus(page, seeded);
    expect(status.status).toBe("metric");
    expect(status.blocking_entities).toEqual([]);
  });

  await test.step("baseline-offset (B) from the same two corners: s 3,00, d 2,00 left", async () => {
    const t = PLAN.triangulation;
    const target = { at: t.expected, chip: `Símbolo en x ${t.expected_x} m · y ${t.expected_y} m` };
    const panel = await pickConstraint(page, BASELINE, target, CORNER_PICKS);
    await expect(page.getByTestId("constraint-baseline")).toBeAttached();
    const b = PLAN.baseline;
    await panel.getByRole("textbox", { name: "Distancia a lo largo de A→B, s (m)" }).fill(b.s);
    await panel.getByRole("textbox", { name: "Distancia perpendicular, d (m)" }).fill(b.d);
    await expect(panel.getByRole("radio", { name: "Izquierda" })).toBeChecked();
    await expect(panel.getByText(`Posición: x ${b.expected_x} m · y ${b.expected_y} m`)).toBeVisible();
    await log.baseline();
    await panel.getByRole("button", { name: "Aplicar", exact: true }).click();
    await expect(panel.getByText("Punto fijado por línea base y offset.")).toBeVisible();
    await log.expectOneUpdate(seeded.marker, "baseline_offset");
    const marker = await entity(page, seeded, seeded.marker);
    expectAt(marker.geometry.anchor_m, b.expected, POINT_TOLERANCE_M);
    expect(marker.metric_origin).toBe("baseline_offset");
    expect(marker.constraint).toMatchObject({
      kind: "baseline_offset",
      refs: [
        { entity_id: wallId, vertex: PLAN.corners.A.vertex },
        { entity_id: wallId, vertex: PLAN.corners.B.vertex },
      ],
      params: { s_m: 3, d_m: 2, side: "left" },
    });
    await expect(evidenceCells(table, 2)).toHaveText([
      PLAN.evidence[1] ?? "",
      b.expected_x,
      b.expected_y,
      "Base y offset",
      /Editar etiqueta/,
    ]);
    await expect(badge(page, "Métrico")).toBeVisible();
    expect((await metricStatus(page, seeded)).status).toBe("metric");
  });
});

test("scaled drawing: triangulation places the evidence within 1 cm and the plan is Métrico", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await logIn(page);
  const seeded = await seed(page, "scaled", PLAN.evidence[1]);
  const log = new EventLog(page, seeded);
  await openEditor(page, seeded);
  expect((await entity(page, seeded, seeded.points[0] ?? "")).metric_origin).toBe("scaled_drawing");

  await test.step("triangulation (G): dA 3,60 from A, dB 4,80 from B", async () => {
    const panel = await pickConstraint(page, TRIANGULATION, MARKER_PICK, POINT_PICKS);
    await expect(panel.getByTestId("constraint-chip")).toHaveCount(3);
    await log.baseline();
    await applyTriangulation(panel);
    await expect(panel.getByText("Punto fijado por triangulación.")).toBeVisible();
    await log.expectOneUpdate(seeded.marker, "triangulate");
    const marker = await entity(page, seeded, seeded.marker);
    expectAt(marker.geometry.anchor_m, PLAN.triangulation.expected, POINT_TOLERANCE_M);
    expect(marker.metric_origin).toBe("triangulation");
    expect(marker.constraint).toMatchObject({
      kind: "triangulation",
      refs: seeded.points,
      params: { d_a_m: 3.6, d_b_m: 4.8 },
    });
  });

  await test.step("evidence table: number, label, coordinates with two decimals and a comma", async () => {
    const table = await openEvidenceTable(page);
    await expect(evidenceCells(table, 1)).toHaveText([
      PLAN.evidence[1] ?? "",
      PLAN.triangulation.expected_x,
      PLAN.triangulation.expected_y,
      "Triangulación",
      /Editar etiqueta/,
    ]);
  });

  await test.step("metric status: Métrico", async () => {
    await expect(badge(page, "Métrico")).toBeVisible();
    const status = await metricStatus(page, seeded);
    expect(status.status).toBe("metric");
    expect(status.blocking_entities).toEqual([]);
  });
});
