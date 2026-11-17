import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  csrfHeaders,
  json,
  logIn,
  rejectChainedSuggestions,
  uniqueToken,
  uploadSource,
  worldToClient,
} from "./support";

// Fix-length on walls drawn as separate lines (walkthrough finding "fix_length breaks corners"):
// the technician draws a rectangle with four separate lines (endpoint snapping closes the
// corners), fixes the top side to a new length with the fix-length tool, and the right wall's top
// end must follow the moved corner. The geometry is checked through GET /revisions/{id}/entities:
// every corner still coincides exactly and the top side has the declared length.
//
// Coordinates are world metres: the scan_segment calibration makes synthetic_sketch.png
// (2000 x 2828 px) cover x 0..20 m and y -28.28..0 m (fixtures/sample_plan.json).

const IMAGE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
const SYNC_TIMEOUT_MS = 15_000;
const EXACT_M = 1e-9;

type Point2 = [number, number];

const TL: Point2 = [2, -4];
const TR: Point2 = [8, -4];
const BR: Point2 = [8, -9];
const BL: Point2 = [2, -9];
const SIDES = { top: [TL, TR], right: [TR, BR], bottom: [BR, BL], left: [BL, TL] } as const;
type Side = keyof typeof SIDES;
const NEW_LENGTH_TEXT = "7,50";
const NEW_LENGTH_M = 7.5;

interface EntityOut {
  id: string;
  type: string;
  geometry: { kind: string; coords_m?: Point2[] };
  deleted_at: string | null;
}

async function liveLines(page: Page, revisionId: string): Promise<EntityOut[]> {
  const body = await json<{ entities: EntityOut[] }>(
    await page.request.get(`/api/v1/revisions/${revisionId}/entities`),
    200,
  );
  return body.entities.filter((entity) => entity.deleted_at === null && entity.type === "line");
}

async function clickAt(page: Page, world: Point2): Promise<void> {
  const { x, y } = await worldToClient(page, world);
  await page.mouse.click(x, y);
}

function near(a: Point2, b: Point2, metres = 0.05): boolean {
  return Math.hypot(a[0] - b[0], a[1] - b[1]) <= metres;
}

/** The synced line drawn for each side, matched by its drawn end points. */
function bySide(lines: EntityOut[]): Record<Side, EntityOut> {
  const result: Partial<Record<Side, EntityOut>> = {};
  for (const [side, [from, to]] of Object.entries(SIDES) as [Side, readonly [Point2, Point2]][]) {
    const line = lines.find((candidate) => {
      const [a, b] = candidate.geometry.coords_m ?? [];
      return a !== undefined && b !== undefined && near(a, from) && near(b, to);
    });
    if (line === undefined) throw new Error(`no line drawn for the ${side} side`);
    result[side] = line;
  }
  return result as Record<Side, EntityOut>;
}

function ends(line: EntityOut): [Point2, Point2] {
  const [a, b] = line.geometry.coords_m ?? [];
  if (a === undefined || b === undefined) throw new Error(`line ${line.id} has no end points`);
  return [a, b];
}

test.use({ viewport: { width: 1600, height: 1000 } });

test("fixing one side of a rectangle of separate lines keeps its corners closed", async ({ page }) => {
  await logIn(page);
  const token = uniqueToken();
  const headers = await csrfHeaders(page);
  const caseFile = await json<{ id: string }>(
    await page.request.post("/api/v1/case-files", {
      headers,
      data: {
        incident_type: "traffic_collision",
        incident_date: "2026-10-01",
        description: `Fijar longitud conectada E2E ${token}`,
        declared_location: `Esquina conectada ${token}`,
      },
    }),
    201,
  );
  const source = await uploadSource(page, caseFile.id, IMAGE);
  const plan = await json<{ id: string; current_revision_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/plans`, {
      headers,
      data: { title: `Rectángulo ${token}`, source_mode: "schematic_sketch", primary_source_id: source.id },
    }),
    201,
  );
  await json(await page.request.post(`/api/v1/plans/${plan.id}/lease`, { headers }), 200);
  await json(
    await page.request.post(`/api/v1/plans/${plan.id}/calibration/accept`, {
      headers,
      data: {
        method: "scan_segment",
        control_points: [{ image_px: [200, 200] }, { image_px: [1200, 200] }],
        segment_mm: 100,
        as_metric: false,
      },
    }),
    200,
  );
  await rejectChainedSuggestions(page, plan.current_revision_id);

  await page.goto(`/cases/${caseFile.id}/plans/${plan.id}/editor`);
  await expect(page.getByTestId("editor-canvas")).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeAttached();
  await expect(page.getByRole("status").filter({ hasText: /^Sincronizado$/ })).toBeVisible();
  const tool = (name: string) => page.getByRole("button", { name, exact: true });

  await test.step("draw the four sides as separate lines", async () => {
    await tool("Línea").click();
    for (const [from, to] of Object.values(SIDES)) {
      await clickAt(page, from);
      await clickAt(page, to);
    }
    await expect
      .poll(async () => (await liveLines(page, plan.current_revision_id)).length, { timeout: SYNC_TIMEOUT_MS })
      .toBe(4);
  });
  const before = bySide(await liveLines(page, plan.current_revision_id));
  // Endpoint snapping closed the corners exactly.
  expect(ends(before.top)[1]).toEqual(ends(before.right)[0]);
  expect(ends(before.right)[1]).toEqual(ends(before.bottom)[0]);
  expect(ends(before.bottom)[1]).toEqual(ends(before.left)[0]);
  expect(ends(before.left)[1]).toEqual(ends(before.top)[0]);

  await test.step("fix the top side to a new length from its left end", async () => {
    await page.keyboard.press("d");
    await expect(tool("Fijar longitud")).toHaveAttribute("aria-pressed", "true");
    await clickAt(page, [(TL[0] + TR[0]) / 2, TL[1]]);
    await expect(page.getByRole("button", { name: "Extremo 1: elegir como ancla" })).toBeAttached();
    await clickAt(page, TL);
    const field = page.getByRole("textbox", { name: "Longitud del segmento (m)" });
    await expect(field).toBeFocused();
    await field.fill(NEW_LENGTH_TEXT);
    const patched = page.waitForResponse(
      (response) => response.request().method() === "PATCH" && response.url().includes(before.top.id),
    );
    await tool("Aplicar").click();
    const body = (await (await patched).json()) as {
      connected: { moved: { entity_id: string; vertex: number }[]; pinned: unknown[] };
    };
    expect(body.connected.moved).toEqual([
      expect.objectContaining({ entity_id: before.right.id, vertex: 0 }),
    ]);
    expect(body.connected.pinned).toEqual([]);
    await expect(
      page.getByText(
        `Longitud fijada en ${NEW_LENGTH_TEXT} m. Se movió 1 vértice conectado (1 entidad: ${before.right.id.slice(0, 8)}).`,
      ),
    ).toBeVisible();
  });

  const after = bySideAfter(await liveLines(page, plan.current_revision_id), before);
  const [topStart, topEnd] = ends(after.top);
  expect(topStart).toEqual(ends(before.top)[0]);
  expect(Math.abs(Math.hypot(topEnd[0] - topStart[0], topEnd[1] - topStart[1]) - NEW_LENGTH_M)).toBeLessThan(
    EXACT_M,
  );
  // The right wall's top end followed the moved corner; every other end stayed.
  expect(ends(after.right)[0]).toEqual(topEnd);
  expect(ends(after.right)[1]).toEqual(ends(before.right)[1]);
  expect(ends(after.bottom)).toEqual(ends(before.bottom));
  expect(ends(after.left)).toEqual(ends(before.left));
  expect(ends(after.right)[1]).toEqual(ends(after.bottom)[0]);
  expect(ends(after.bottom)[1]).toEqual(ends(after.left)[0]);
  expect(ends(after.left)[1]).toEqual(ends(after.top)[0]);
});

/** The lines after the fix, by the ids found before it. */
function bySideAfter(lines: EntityOut[], before: Record<Side, EntityOut>): Record<Side, EntityOut> {
  const find = (side: Side) => {
    const line = lines.find((candidate) => candidate.id === before[side].id);
    if (line === undefined) throw new Error(`the ${side} line is gone`);
    return line;
  };
  return { top: find("top"), right: find("right"), bottom: find("bottom"), left: find("left") };
}
