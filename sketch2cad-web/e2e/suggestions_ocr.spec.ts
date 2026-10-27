import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  auditPage,
  csrfHeaders,
  json,
  logIn,
  planEvents,
  readCredentials,
  uniqueToken,
  uploadSource,
  worldToClient,
} from "./support";

// AI assistance against the compose stack (H5, US-12, US-13, REQ-17, REQ-18, H5-6, H5-7): the
// `suggest_lines` job chained after the calibration fills the batch panel ('Sugerencias (N)'),
// whose "Aceptar todas" / "Rechazar todas" review every suggestion in one request; the OCR crop
// tool (R) reads the written dimension of the fixture, and the accepted reading becomes a
// dimension with `source.method = ocr`. With `ai_assist.enabled` switched off by an admin
// (runtime override) the controls are absent and the AI jobs give 404.
//
// The OCR reading needs the worker built with the TrOCR model: run the stack with
// E2E_WITH_OCR=1 (scripts/e2e-stack.sh). Without it the worker has no model, so the reading
// tests are skipped and the failed-job test (H5-6) runs instead, since every OCR job then fails
// deterministically with "OCR no disponible".
//
// Fixture and layout: fixtures/dimensioned_sketch.png and .json, both written by
// fixtures/make_dimensioned_sketch.py. Each test seeds its own case and plan through the API.

const FIXTURE = path.join(import.meta.dirname, "fixtures", "dimensioned_sketch.png");
const LAYOUT = JSON.parse(
  readFileSync(path.join(import.meta.dirname, "fixtures", "dimensioned_sketch.json"), "utf8"),
) as Layout;
const WITH_OCR = process.env.E2E_WITH_OCR === "1";
const NO_OCR_REASON = "the worker has no OCR model: run the stack with E2E_WITH_OCR=1";
/** QC, rectify and suggest_lines jobs, one after the other, on a possibly cold worker. */
const SUGGESTIONS_TIMEOUT_MS = 120_000;
/** First OCR job of a worker may wait for the model load (~5 s) on top of the reading. */
const OCR_TIMEOUT_MS = 90_000;
const SYNC_TIMEOUT_MS = 15_000;
const AI_PARAM = "ai_assist.enabled";
const BATCH_TITLE = "Sugerencias de líneas";
const OCR_TITLE = "Leer cota (OCR)";
/** MODEL_UNAVAILABLE of sketch2cad.worker.tasks_ocr. */
const OCR_UNAVAILABLE = {
  cause: "El reconocimiento de texto (OCR) no está disponible en este servidor.",
  action: "Escribe el valor de la cota a mano; avisa al administrador para que revise el worker.",
};

type Point2 = [number, number];
type BoxPx = [number, number, number, number];

interface Layout {
  size_px: [number, number];
  dimension: { text: string; value_m: number; box_px: BoxPx };
  top_wall_px: [Point2, Point2];
  blank_box_px: BoxPx;
}

interface Seeded {
  caseId: string;
  planId: string;
  revisionId: string;
  sourceId: string;
}

interface EntityOut {
  id: string;
  type: string;
  geometry: { kind: string; coords_m?: Point2[]; declared_value_m?: number | null };
  source: { method: string; job_id: string | null };
  review_status: string;
  metric_origin: string;
  deleted_at: string | null;
}

interface JobOut {
  id: string;
  kind: string;
  status: string;
  error: { cause: string; action: string } | null;
  result: {
    raw_text: string;
    value_m: number | null;
    confidence: number;
    preselect: boolean;
    min_confidence: number;
  } | null;
}

/** Case, uploaded fixture (QC done), plan, lease and an accepted calibration (plan EDITING). */
async function seedCalibratedPlan(page: Page, purpose: string): Promise<Seeded> {
  const token = uniqueToken();
  const headers = await csrfHeaders(page);
  const caseFile = await json<{ id: string }>(
    await page.request.post("/api/v1/case-files", {
      headers,
      data: {
        incident_type: "traffic_collision",
        incident_date: "2026-10-01",
        description: `Asistencia IA E2E (${purpose}) ${token}`,
        declared_location: `Cruce de asistencia IA ${token}`,
      },
    }),
    201,
  );
  const source = await uploadSource(page, caseFile.id, FIXTURE);
  const plan = await json<{ id: string; current_revision_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/plans`, {
      headers,
      data: { title: `Plano IA ${token}`, primary_source_id: source.id, source_mode: "schematic_sketch" },
    }),
    201,
  );
  await json(await page.request.post(`/api/v1/plans/${plan.id}/lease`, { headers }), 200);
  // Same calibration as editor_a11y.spec.ts: the PNG has no DPI, so `scan_segment` with two
  // points 1000 px apart that span 100 mm of paper (10 px/mm, the fixture's design scale).
  const accepted = await json<{ status: string }>(
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
  expect(accepted.status).toBe("EDITING");
  return {
    caseId: caseFile.id,
    planId: plan.id,
    revisionId: plan.current_revision_id,
    sourceId: source.id,
  };
}

async function entities(page: Page, seeded: Seeded): Promise<EntityOut[]> {
  const body = await json<{ entities: EntityOut[] }>(
    await page.request.get(`/api/v1/revisions/${seeded.revisionId}/entities`),
    200,
  );
  return body.entities;
}

/** Ids of the pending suggestions, once the chained suggest_lines job has inserted them. */
async function waitForSuggestions(page: Page, seeded: Seeded): Promise<string[]> {
  const pending = async () =>
    (await entities(page, seeded))
      .filter((entity) => entity.review_status === "suggested" && entity.deleted_at === null)
      .map((entity) => entity.id);
  await expect
    .poll(async () => (await pending()).length, {
      timeout: SUGGESTIONS_TIMEOUT_MS,
      message: "suggest_lines inserted suggestions after the calibration",
    })
    .toBeGreaterThan(0);
  return pending();
}

/** Open the editor and wait until `GET /config/features` answered; returns the switch. */
async function openEditor(page: Page, seeded: Seeded): Promise<boolean> {
  const features = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/config/features"),
  );
  await page.goto(`/cases/${seeded.caseId}/plans/${seeded.planId}/editor`);
  const enabled = await json<{ ai_assist: { enabled: boolean } }>(await features, 200);
  await expect(page.getByTestId("editor-canvas")).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeAttached();
  await expect(page.getByRole("status").filter({ hasText: /^Sincronizado$/ })).toBeVisible();
  return enabled.ai_assist.enabled;
}

/** Client position of an original-image pixel: the canvas stretches the image over its box. */
async function imagePxToClient(page: Page, [px, py]: Point2): Promise<{ x: number; y: number }> {
  const image = page.getByTestId("source-image");
  const read = async () => {
    const box = await image.boundingBox();
    if (box === null) throw new Error("the source image is not rendered");
    return box;
  };
  // The canvas settles its size (and the fit) after mount: wait for two equal reads.
  let box = await read();
  await expect
    .poll(async () => {
      const next = await read();
      const same = JSON.stringify(next) === JSON.stringify(box);
      box = next;
      return same;
    })
    .toBe(true);
  const [width, height] = LAYOUT.size_px;
  return { x: box.x + (px / width) * box.width, y: box.y + (py / height) * box.height };
}

/** Drag the OCR box over `box` (original px) and return the job the tool queued. */
async function dragOcrBox(page: Page, [x0, y0, x1, y1]: BoxPx): Promise<string> {
  const a = await imagePxToClient(page, [x0, y0]);
  const b = await imagePxToClient(page, [x1, y1]);
  const canvas = await page.getByTestId("editor-canvas").boundingBox();
  if (canvas === null) throw new Error("the canvas is not rendered");
  for (const p of [a, b]) {
    expect(p.x > canvas.x && p.x < canvas.x + canvas.width, "box corner inside the canvas").toBe(true);
    expect(p.y > canvas.y && p.y < canvas.y + canvas.height, "box corner inside the canvas").toBe(true);
  }
  const posted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && /\/api\/v1\/plans\/[^/]+\/jobs$/.test(response.url()),
  );
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(b.x, b.y, { steps: 5 });
  await page.mouse.up();
  const job = await json<JobOut>(await posted, 202);
  expect(job.kind).toBe("ocr_crop");
  return job.id;
}

async function finishedJob(page: Page, jobId: string): Promise<JobOut> {
  await expect
    .poll(async () => (await json<JobOut>(await page.request.get(`/api/v1/jobs/${jobId}`), 200)).status, {
      timeout: OCR_TIMEOUT_MS,
      message: "OCR job finished",
    })
    .toMatch(/^(succeeded|failed)$/);
  return json<JobOut>(await page.request.get(`/api/v1/jobs/${jobId}`), 200);
}

async function selectOcrTool(page: Page) {
  await page.getByRole("button", { name: OCR_TITLE, exact: true }).click();
  await expect(page.getByRole("button", { name: OCR_TITLE, exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const panel = page.getByRole("complementary", { name: OCR_TITLE });
  await expect(panel).toBeVisible();
  return panel;
}

/** A request context logged in as the E2E admin (scripts/e2e-stack.sh `admin_credentials`). */
async function adminSession(baseURL: string | undefined): Promise<APIRequestContext> {
  const admin = await request.newContext({ baseURL });
  await json(await admin.post("/api/v1/auth/login", { data: readCredentials("admin_credentials") }), 200);
  return admin;
}

/** Remove the global override of `ai_assist.enabled` (404 when there is none). */
async function restoreAiAssist(admin: APIRequestContext): Promise<number> {
  const response = await admin.delete(`/api/v1/config/params/${AI_PARAM}`, {
    headers: await csrfHeaders(admin),
    params: { scope: "global" },
  });
  expect([200, 404], await response.text()).toContain(response.status());
  return response.status();
}

test.use({ viewport: { width: 1600, height: 1000 } });

test.describe("AI assistance on", () => {
  test.beforeEach(async ({ page }) => {
    await logIn(page);
  });

  test("suggestions arrive after the calibration and are accepted in bulk", async ({ page }) => {
    test.setTimeout(240_000);
    const seeded = await seedCalibratedPlan(page, "aceptar sugerencias");
    const ids = await waitForSuggestions(page, seeded);
    const n = ids.length;

    await test.step("one suggestions.added event lists every suggestion", async () => {
      const added = (await planEvents(page, seeded.caseId, seeded.planId)).filter(
        (event) => event.kind === "suggestions.added",
      );
      expect(added).toHaveLength(1);
      expect(added[0]?.after).toMatchObject({ count: n });
      expect([...((added[0]?.after?.entity_ids as string[] | undefined) ?? [])].sort()).toEqual(
        [...ids].sort(),
      );
    });

    expect(await openEditor(page, seeded)).toBe(true);
    const toggle = page.getByRole("button", { name: `Sugerencias (${String(n)})`, exact: true });
    await expect(toggle).toBeVisible();
    await expect(page.getByTestId("suggestion-layer").locator("[data-suggestion='true']")).toHaveCount(n);
    await expect(page.getByRole("button", { name: OCR_TITLE, exact: true })).toBeVisible();

    const panel = page.getByRole("region", { name: BATCH_TITLE });
    await test.step("the batch panel lists them by confidence band", async () => {
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await expect(panel).toBeVisible();
      const high = Number(await panel.getByTestId("band-high").textContent());
      const low = Number(await panel.getByTestId("band-low").textContent());
      expect(high + low).toBe(n);
      await expect(panel.getByRole("list", { name: BATCH_TITLE }).getByRole("listitem")).toHaveCount(n);
      await auditPage(page, "editor with the suggestions panel");
    });

    await test.step("Aceptar todas: every suggestion becomes a normal entity", async () => {
      await panel.getByRole("button", { name: "Aceptar todas", exact: true }).click();
      await expect(panel.getByRole("status")).toHaveText(
        n === 1 ? "Aceptadas: 1 sugerencia." : `Aceptadas: ${String(n)} sugerencias.`,
      );
      await expect(page.getByRole("button", { name: "Sugerencias (0)", exact: true })).toBeVisible();
      await expect(panel.getByText("No hay sugerencias pendientes.")).toBeVisible();
      await expect(page.getByTestId("suggestion-layer").locator("[data-suggestion='true']")).toHaveCount(0);
      const reviewed = (await entities(page, seeded)).filter((entity) => ids.includes(entity.id));
      expect(reviewed).toHaveLength(n);
      for (const entity of reviewed) {
        expect(entity.review_status).toBe("accepted");
        expect(entity.deleted_at).toBeNull();
        expect(entity.source.method).toBe("cv");
        expect(entity.metric_origin).toBe("trace_only");
        // Drawn by the normal entity layers now, not by the suggestion layer.
        await expect(page.locator(`[data-entity-id="${entity.id}"]:not([data-suggestion])`)).toBeAttached();
      }
    });
  });

  test("Rechazar todas removes every suggestion", async ({ page }) => {
    test.setTimeout(240_000);
    const seeded = await seedCalibratedPlan(page, "rechazar sugerencias");
    const ids = await waitForSuggestions(page, seeded);
    const n = ids.length;
    await openEditor(page, seeded);
    await page.getByRole("button", { name: `Sugerencias (${String(n)})`, exact: true }).click();
    const panel = page.getByRole("region", { name: BATCH_TITLE });
    await panel.getByRole("button", { name: "Rechazar todas", exact: true }).click();
    await expect(panel.getByRole("status")).toHaveText(
      n === 1 ? "Rechazadas: 1 sugerencia." : `Rechazadas: ${String(n)} sugerencias.`,
    );
    await expect(page.getByRole("button", { name: "Sugerencias (0)", exact: true })).toBeVisible();
    await expect(page.locator("[data-entity-id]")).toHaveCount(0);
    // Rejected suggestions are soft-deleted: gone from the revision, one review event each.
    expect((await entities(page, seeded)).filter((entity) => ids.includes(entity.id))).toEqual([]);
    const reviewed = (await planEvents(page, seeded.caseId, seeded.planId)).filter(
      (event) => event.kind === "entity.reviewed",
    );
    expect(reviewed.map((event) => event.entity_id).sort()).toEqual([...ids].sort());
    for (const event of reviewed) expect(event.after).toMatchObject({ review_status: "rejected" });
  });

  test("OCR reads the written dimension and the accepted value becomes an OCR dimension", async ({
    page,
  }) => {
    test.skip(!WITH_OCR, NO_OCR_REASON);
    test.setTimeout(300_000);
    const seeded = await seedCalibratedPlan(page, "OCR");
    // Review the suggestions first (the bulk UI is covered above): the walls become entities.
    const ids = await waitForSuggestions(page, seeded);
    await json(
      await page.request.post(`/api/v1/revisions/${seeded.revisionId}/entities:bulk-review`, {
        headers: await csrfHeaders(page),
        data: { ids, review_status: "accepted" },
      }),
      200,
    );
    await openEditor(page, seeded);
    const panel = await selectOcrTool(page);
    await expect(panel.getByRole("status").first()).toHaveText(/^Arrastra un recuadro/);
    await auditPage(page, "editor with the OCR panel");

    const form = panel.getByRole("form", { name: OCR_TITLE });
    const field = form.getByRole("textbox", { name: "Medida (m)" });
    let jobId = "";
    await test.step("box over the written dimension: the reading is preselected", async () => {
      jobId = await dragOcrBox(page, LAYOUT.dimension.box_px);
      await expect(panel.getByText("Lectura lista.", { exact: false })).toBeVisible({
        timeout: OCR_TIMEOUT_MS,
      });
      const job = await finishedJob(page, jobId);
      expect(job.status).toBe("succeeded");
      const result = job.result;
      if (result === null) throw new Error("the OCR job has no result");
      console.log(
        `[ocr] fixture '${LAYOUT.dimension.text}' read as '${result.raw_text}' ` +
          `value_m=${String(result.value_m)} confidence=${String(result.confidence)}`,
      );
      expect(result.value_m).not.toBeNull();
      expect(result.value_m ?? 0).toBeCloseTo(LAYOUT.dimension.value_m, 6);
      expect(result.preselect).toBe(true);
      expect(result.confidence).toBeGreaterThanOrEqual(result.min_confidence);
      await expect(form.getByText("Valor propuesto: 3,50 m", { exact: true })).toBeVisible();
      const ratio = (value: number) => value.toFixed(2).replace(".", ",");
      await expect(
        form.getByText(
          `Confianza: ${ratio(result.confidence)} (mínima para proponer: ${ratio(result.min_confidence)})`,
          { exact: true },
        ),
      ).toBeVisible();
      await expect(field).toHaveValue("3,5");
      await expect(field).toBeFocused();
      await auditPage(page, "editor with an OCR reading");
    });

    await test.step("pick the top wall and create the dimension", async () => {
      const wall = await topWallSegment(page, seeded);
      const at = await worldToClient(page, [(wall[0][0] + wall[1][0]) / 2, (wall[0][1] + wall[1][1]) / 2]);
      await page.mouse.click(at.x, at.y);
      await expect(form.getByText(/^Destino: segmento \d+ \(/)).toBeVisible();
      await form.getByRole("button", { name: "Crear cota", exact: true }).click();
      await expect(panel.getByText("Cota creada: 3,50 m (lectura OCR aceptada).", { exact: true })).toBeVisible({
        timeout: SYNC_TIMEOUT_MS,
      });
      const dimensions = (await entities(page, seeded)).filter(
        (entity) => entity.type === "dimension" && entity.deleted_at === null,
      );
      expect(dimensions).toHaveLength(1);
      const dimension = dimensions[0] as EntityOut;
      expect(dimension.source).toMatchObject({ method: "ocr", job_id: jobId });
      expect(dimension.geometry.declared_value_m).toBeCloseTo(LAYOUT.dimension.value_m, 9);
      const reviewed = (await planEvents(page, seeded.caseId, seeded.planId)).filter(
        (event) => event.kind === "entity.reviewed" && event.entity_id === dimension.id,
      );
      expect(reviewed.map((event) => event.after?.review_status)).toEqual(["accepted"]);
      await expect(page.locator(`[data-entity-id="${dimension.id}"]`)).toBeAttached();
    });

    await test.step("box over blank paper: no value is preselected (H5-6, low confidence)", async () => {
      const blankJob = await dragOcrBox(page, LAYOUT.blank_box_px);
      await expect(panel.getByText("Lectura lista.", { exact: false })).toBeVisible({
        timeout: OCR_TIMEOUT_MS,
      });
      const job = await finishedJob(page, blankJob);
      expect(job.status).toBe("succeeded");
      console.log(
        `[ocr] blank paper read as '${job.result?.raw_text ?? ""}' ` +
          `value_m=${String(job.result?.value_m)} confidence=${String(job.result?.confidence)}`,
      );
      expect(job.result?.preselect).toBe(false);
      await expect(field).toHaveValue("");
      await expect(field).not.toBeFocused();
      await expect(form.getByText(/^Lectura sin proponer: «/)).toBeVisible();
      await form.getByRole("button", { name: "Descartar", exact: true }).click();
      await expect(form).toHaveCount(0);
    });
  });

  test("a failed OCR job shows its cause and action and the editor stays usable (H5-6)", async ({
    page,
  }) => {
    test.skip(WITH_OCR, "the OCR worker reads the crop; this checks the worker without the model");
    test.setTimeout(240_000);
    const seeded = await seedCalibratedPlan(page, "OCR fallido");
    await openEditor(page, seeded);
    const panel = await selectOcrTool(page);
    const jobId = await dragOcrBox(page, LAYOUT.dimension.box_px);
    const alert = panel.getByRole("alert").filter({ hasText: "El trabajo falló" });
    await expect(alert).toBeVisible({ timeout: OCR_TIMEOUT_MS });
    await expect(alert).toContainText(`Causa: ${OCR_UNAVAILABLE.cause}`);
    await expect(alert).toContainText(`Qué hacer: ${OCR_UNAVAILABLE.action}`);
    await expect(panel.getByText("La lectura falló. Puedes seguir editando o recortar de nuevo.")).toBeVisible();
    const job = await finishedJob(page, jobId);
    expect(job.status).toBe("failed");
    expect(job.error).toEqual(OCR_UNAVAILABLE);
    await auditPage(page, "editor with a failed OCR job");
    await alert.getByRole("button", { name: "Descartar aviso", exact: true }).click();
    await expect(alert).toHaveCount(0);

    // Still editable: a line drawn with the line tool reaches the server.
    await page.getByRole("button", { name: "Línea", exact: true }).click();
    const [x0, y0] = LAYOUT.blank_box_px;
    const a = await imagePxToClient(page, [x0, y0]);
    await page.mouse.click(a.x, a.y);
    await page.mouse.click(a.x + 120, a.y);
    await expect
      .poll(
        async () =>
          (await entities(page, seeded)).filter((e) => e.type === "line" && e.deleted_at === null).length,
        { timeout: SYNC_TIMEOUT_MS },
      )
      .toBe(1);
  });
});

test.describe("AI assistance off (H5-7)", () => {
  let admin: APIRequestContext | undefined;

  test.afterAll(async () => {
    // Safety net: whatever happened in the test, other specs see the switch at its default.
    if (admin !== undefined) {
      await restoreAiAssist(admin);
      await admin.dispose();
    }
  });

  test("with ai_assist.enabled=false the controls are absent and the AI jobs give 404", async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(240_000);
    const session = await adminSession(baseURL);
    admin = session;
    await logIn(page);
    const features = async () =>
      (await json<{ ai_assist: { enabled: boolean } }>(await page.request.get("/api/v1/config/features"), 200))
        .ai_assist.enabled;
    expect(await features(), "the switch starts on (no override left by another spec)").toBe(true);

    try {
      await test.step("an admin switches the AI assistance off (global runtime override)", async () => {
        const param = await json<{ overrides: { scope: string; value: unknown }[] }>(
          await session.put(`/api/v1/config/params/${AI_PARAM}`, {
            headers: await csrfHeaders(session),
            data: { value: false, scope: "global" },
          }),
          200,
        );
        expect(param.overrides).toEqual([expect.objectContaining({ scope: "global", value: false })]);
        expect(await features()).toBe(false);
      });

      const seeded = await seedCalibratedPlan(page, "IA desactivada");
      await test.step("fresh editor page: no suggestions button, no OCR tool", async () => {
        expect(await openEditor(page, seeded)).toBe(false);
        // The palette and the header are rendered (the editor is ready) ...
        await expect(page.getByRole("button", { name: "Línea", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: /^Estado métrico: / })).toBeVisible();
        // ... without any AI control.
        await expect(page.getByRole("button", { name: /^Sugerencias/ })).toHaveCount(0);
        await expect(page.getByRole("button", { name: OCR_TITLE, exact: true })).toHaveCount(0);
        await expect(page.getByTestId("suggestion-layer")).toHaveCount(0);
        await page.getByTestId("editor-canvas").focus();
        await page.keyboard.press("r");
        await expect(page.getByRole("complementary", { name: OCR_TITLE })).toHaveCount(0);
      });

      await test.step("POST /plans/{id}/jobs gives 404 for both AI kinds", async () => {
        const headers = await csrfHeaders(page);
        const bodies = [
          { kind: "suggest_lines", params: {} },
          { kind: "ocr_crop", params: { source_id: seeded.sourceId, box_px: LAYOUT.dimension.box_px } },
        ];
        for (const data of bodies) {
          const response = await page.request.post(`/api/v1/plans/${seeded.planId}/jobs`, { headers, data });
          expect(response.status(), `${data.kind}: ${await response.text()}`).toBe(404);
        }
        // No suggest_lines was chained after this calibration either.
        expect((await entities(page, seeded)).filter((e) => e.review_status === "suggested")).toEqual([]);
      });
    } finally {
      expect(await restoreAiAssist(session)).toBe(200);
    }
    expect(await features(), "the switch is back on").toBe(true);
  });
});

/** World ends of the accepted segment that runs along the fixture's top wall. */
async function topWallSegment(page: Page, seeded: Seeded): Promise<[Point2, Point2]> {
  const [p, q] = LAYOUT.top_wall_px;
  const wallMid = await imagePxToClient(page, [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2]);
  let best: { ends: [Point2, Point2]; px: number } | null = null;
  for (const entity of await entities(page, seeded)) {
    if (entity.deleted_at !== null || entity.review_status !== "accepted") continue;
    const coords = entity.geometry.coords_m ?? [];
    for (let i = 0; i + 1 < coords.length; i += 1) {
      const a = coords[i] as Point2;
      const b = coords[i + 1] as Point2;
      const mid = await worldToClient(page, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
      const px = Math.hypot(mid.x - wallMid.x, mid.y - wallMid.y);
      if (best === null || px < best.px) best = { ends: [a, b], px };
    }
  }
  if (best === null) throw new Error("no accepted segment");
  // The suggestion follows the stroke within a few screen pixels.
  expect(best.px, "an accepted segment runs along the top wall").toBeLessThan(20);
  return best.ends;
}
