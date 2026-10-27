import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  auditPage,
  csrfHeaders,
  json,
  readCredentials,
  rejectChainedSuggestions,
  uniqueToken,
  uploadSource,
  worldToClient,
} from "./support";

// The "Cota" tool end to end (PRD CU-04, spec 8.2 and 13): the technician draws a wall with the
// line tool, picks it with the dimension tool, types the declared value with a decimal comma and
// creates the dimension; the editor syncs it to L_DIMENSIONS. The reviewer validates the plan and
// runs the final export through the API; the downloaded DXF must hold an aligned DIMENSION on
// L_DIMENSIONS whose text is the declared value (alert style, as it differs from the drawn length
// beyond the 0.02 m tolerance) and the PDF plan must print that value. The DXF and the PDF are read
// with ezdxf and pypdfium2 from the API's locked environment (`uv` on PATH, as export_verify).
//
// Coordinates are world metres: the scan_segment calibration makes synthetic_sketch.png
// (2000 x 2828 px) cover x 0..20 m and y -28.28..0 m (fixtures/sample_plan.json).

const IMAGE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
const API_DIR = path.join(import.meta.dirname, "..", "..", "sketch2cad-api");
const SYNC_TIMEOUT_MS = 15_000;
const EXPORT_TIMEOUT_MS = 120_000;
/** The first `uv run` may sync the API's virtual environment. */
const READ_TIMEOUT_MS = 180_000;
const WALL: [Point2, Point2] = [[2, -6], [10, -6]];
const DECLARED_TEXT = "7,95";
const DECLARED_M = 7.95;

type Point2 = [number, number];

interface EntityOut {
  id: string;
  type: string;
  layer: string;
  geometry: Record<string, unknown> & { coords_m?: Point2[] };
  deleted_at: string | null;
}

/** Reads the DIMENSION entities of a DXF and the text of the first PDF page; prints JSON. */
const READ_EXPORTS = `
import io, json, sys
import pypdfium2 as pdfium
from ezdxf.filemanagement import read
with open(sys.argv[1], encoding="utf-8") as handle:
    doc = read(io.StringIO(handle.read()))
dimensions = [
    {"text": d.dxf.text, "layer": d.dxf.layer, "dimstyle": d.dxf.dimstyle, "kind": d.dxf.dimtype & 7}
    for d in doc.modelspace().query("DIMENSION")
]
page_text = pdfium.PdfDocument(sys.argv[2])[0].get_textpage().get_text_range()
print(json.dumps({"dimensions": dimensions, "pdf_text": page_text}))
`;

interface ExportContents {
  dimensions: { text: string; layer: string; dimstyle: string; kind: number }[];
  pdf_text: string;
}

async function liveEntities(page: Page, revisionId: string): Promise<EntityOut[]> {
  const body = await json<{ entities: EntityOut[] }>(
    await page.request.get(`/api/v1/revisions/${revisionId}/entities`),
    200,
  );
  return body.entities.filter((entity) => entity.deleted_at === null);
}

async function clickAt(page: Page, world: Point2): Promise<void> {
  const { x, y } = await worldToClient(page, world);
  await page.mouse.click(x, y);
}

async function transition(page: Page, planId: string, to: string): Promise<void> {
  await json(
    await page.request.post(`/api/v1/plans/${planId}/transition`, {
      headers: await csrfHeaders(page),
      data: { to },
    }),
    200,
  );
}

test.use({ viewport: { width: 1600, height: 1000 } });

test("a dimension created with the Cota tool prints its declared value in the DXF and the PDF", async ({
  page,
  browser,
}) => {
  test.setTimeout(EXPORT_TIMEOUT_MS + READ_TIMEOUT_MS + 120_000);
  const technician = readCredentials();
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(technician.username);
  await page.getByLabel("Contraseña").fill(technician.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Expedientes" })).toBeVisible();

  const token = uniqueToken();
  const headers = await csrfHeaders(page);
  const caseFile = await json<{ id: string }>(
    await page.request.post("/api/v1/case-files", {
      headers,
      data: {
        incident_type: "traffic_collision",
        incident_date: "2026-10-01",
        description: `Cota E2E ${token}`,
        declared_location: `Cruce de la cota ${token}`,
      },
    }),
    201,
  );
  const source = await uploadSource(page, caseFile.id, IMAGE);
  const plan = await json<{ id: string; current_revision_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/plans`, {
      headers,
      data: { title: `Plano con cota ${token}`, source_mode: "schematic_sketch", primary_source_id: source.id },
    }),
    201,
  );
  await json(await page.request.post(`/api/v1/plans/${plan.id}/lease`, { headers }), 200);
  const calibrated = await json<{ status: string }>(
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
  expect(calibrated.status).toBe("EDITING");
  // Not about AI assistance: drop the suggestions chained after the calibration.
  await rejectChainedSuggestions(page, plan.current_revision_id);

  await page.goto(`/cases/${caseFile.id}/plans/${plan.id}/editor`);
  await expect(page.getByTestId("editor-canvas")).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeAttached();
  const synced = page.getByRole("status").filter({ hasText: /^Sincronizado$/ });
  await expect(synced).toBeVisible();
  const tool = (name: string) => page.getByRole("button", { name, exact: true });

  await test.step("draw the wall with the line tool", async () => {
    await tool("Línea").click();
    await clickAt(page, WALL[0]);
    await clickAt(page, WALL[1]);
    await expect
      .poll(async () => (await liveEntities(page, plan.current_revision_id)).filter((e) => e.type === "line"), {
        timeout: SYNC_TIMEOUT_MS,
      })
      .toHaveLength(1);
  });
  const [wall] = (await liveEntities(page, plan.current_revision_id)).filter((e) => e.type === "line");
  if (wall === undefined) throw new Error("the wall was not synced");

  await test.step("pick the wall with the Cota tool and create the dimension", async () => {
    await page.keyboard.press("a");
    await expect(tool("Cota")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Cota: haz clic en el primer punto o en un segmento")).toBeVisible();
    await clickAt(page, [(WALL[0][0] + WALL[1][0]) / 2, WALL[0][1]]);
    const form = page.getByRole("form", { name: "Nueva cota" });
    await expect(form).toBeVisible();
    await expect(form).toContainText("Medida en el plano: 8,00 m");
    await expect(form.getByLabel("Separación de la línea de cota (m)")).toHaveValue("0,50");
    await auditPage(page, "dimension form");
    await form.getByLabel("Valor declarado de la cota (m)").fill(DECLARED_TEXT);
    await form.getByRole("button", { name: "Crear cota" }).click();
    await expect(page.getByText("Cota de 7,95 m creada.")).toBeVisible();
    await expect
      .poll(
        async () => (await liveEntities(page, plan.current_revision_id)).filter((e) => e.type === "dimension"),
        { timeout: SYNC_TIMEOUT_MS },
      )
      .toHaveLength(1);
  });

  const [dimension] = (await liveEntities(page, plan.current_revision_id)).filter((e) => e.type === "dimension");
  expect(dimension?.layer).toBe("L_DIMENSIONS");
  expect(dimension?.geometry).toMatchObject({
    kind: "dimension",
    p1_m: wall.geometry.coords_m?.[0],
    p2_m: wall.geometry.coords_m?.[1],
    offset_m: 0.5,
    declared_value_m: DECLARED_M,
  });
  // 7.95 declared on an 8.00 m wall: the canvas warns and shows the measured length. The
  // canvas keys an entity drawn in this session by its local id until a reload.
  const drawn = page.locator('[data-layer="L_DIMENSIONS"] [data-kind="dimension"]');
  await expect(drawn).toBeAttached();
  await expect(drawn).toContainText("7,95 m");
  await expect(drawn.getByTestId("dimension-discrepancy")).toContainText("medida 8,00 m");

  const reviewerContext = await browser.newContext();
  const workDir = mkdtempSync(path.join(os.tmpdir(), "sketch2cad-dimension-"));
  try {
    const reviewer = await reviewerContext.newPage();
    const credentials = readCredentials("reviewer_credentials");
    await reviewer.goto("/login");
    await reviewer.getByLabel("Usuario").fill(credentials.username);
    await reviewer.getByLabel("Contraseña").fill(credentials.password);
    await reviewer.getByRole("button", { name: "Entrar" }).click();
    await expect(reviewer.getByRole("heading", { level: 1, name: "Expedientes" })).toBeVisible();
    const me = await json<{ user: { id: string } }>(await reviewer.request.get("/api/v1/auth/me"), 200);
    await json(
      await page.request.post(`/api/v1/case-files/${caseFile.id}/members`, {
        headers,
        data: { user_id: me.user.id, permissions: ["read", "edit", "deliver"] },
      }),
      201,
    );
    await transition(page, plan.id, "REVIEW_PENDING");
    await transition(reviewer, plan.id, "IN_REVIEW");
    await transition(reviewer, plan.id, "VALIDATED");

    const job = await json<{ id: string }>(
      await reviewer.request.post(`/api/v1/plans/${plan.id}/exports`, {
        headers: await csrfHeaders(reviewer),
        data: { mode: "final" },
      }),
      202,
    );
    await expect
      .poll(
        async () => (await json<{ status: string }>(await reviewer.request.get(`/api/v1/jobs/${job.id}`), 200)).status,
        { timeout: EXPORT_TIMEOUT_MS, message: "final export job" },
      )
      .toBe("succeeded");
    const artifacts = await json<{ id: string; kind: string }[]>(
      await reviewer.request.get(`/api/v1/plans/${plan.id}/artifacts`),
      200,
    );
    const files: Record<string, string> = {};
    for (const kind of ["dxf", "pdf_plan"]) {
      const artifact = artifacts.find((candidate) => candidate.kind === kind);
      if (artifact === undefined) throw new Error(`the final export produced no ${kind}`);
      const download = await reviewer.request.get(`/api/v1/artifacts/${artifact.id}`, {
        params: { confirm: "true" },
      });
      expect(download.status(), await download.text()).toBe(200);
      files[kind] = path.join(workDir, `plan.${kind === "dxf" ? "dxf" : "pdf"}`);
      writeFileSync(files[kind], await download.body());
    }

    const result = spawnSync(
      "uv",
      ["run", "--quiet", "--frozen", "--project", API_DIR, "python", "-I", "-c", READ_EXPORTS, files.dxf ?? "", files.pdf_plan ?? ""],
      { encoding: "utf8", timeout: READ_TIMEOUT_MS },
    );
    if (result.error !== undefined) throw result.error;
    expect(result.status, result.stderr).toBe(0);
    const contents = JSON.parse(result.stdout) as ExportContents;
    expect(contents.dimensions).toEqual([
      { text: "7.95", layer: "L_DIMENSIONS", dimstyle: "S2C_DIM_ALERT", kind: 1 },
    ]);
    expect(contents.pdf_text).toContain("7.95");
  } finally {
    await reviewerContext.close();
    rmSync(workDir, { recursive: true, force: true });
  }
});
