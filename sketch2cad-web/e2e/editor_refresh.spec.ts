import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { csrfHeaders, json, logIn, uniqueToken, uploadSource } from "./support";

// The editor follows the jobs of the calibration it was opened from (GET /plans/{id}/jobs):
//
// - after the calibration is accepted in the wizard, the `rectify` job and the chained
//   `suggest_lines` job finish a few seconds after the editor opened; the editor reloads the
//   entities on its own, so 'Sugerencias (N)' reaches N > 0 without reloading the page;
// - a calibration whose rectified image would be too large makes `rectify` fail; the editor
//   shows the job's cause and action and a link back to the calibration.
//
// Fixture: fixtures/synthetic_sketch.png, 2000 x 2828 px without DPI (26 suggestions). The
// wizard marks two points 1000 px apart on the image (10 % and 60 % of its width): a 100 mm
// segment gives 10 px/mm (the fixture's design scale); a 1000 mm segment makes the rectified
// canvas 20000 x 28280 px, above Pillow's limit (~89.5 Mpx), so `rectify` fails with
// "La imagen rectificada sería demasiado grande para generarse."

const FIXTURE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
/** rectify and then suggest_lines on a cold worker. */
const JOBS_TIMEOUT_MS = 120_000;
const TOO_LARGE_CAUSE = "La imagen rectificada sería demasiado grande para generarse.";
const TOO_LARGE_ACTION =
  "Revisa las dimensiones conocidas o los puntos de calibración y vuelve a aceptarla.";
const RECTIFY_FAILED = "No se pudo generar la imagen rectificada; no habrá sugerencias automáticas";

interface Seeded {
  caseId: string;
  planId: string;
  revisionId: string;
}

/** Case, uploaded fixture (QC done) and a plan still waiting for its calibration. */
async function seedPlan(page: Page, purpose: string): Promise<Seeded> {
  const token = uniqueToken();
  const headers = await csrfHeaders(page);
  const caseFile = await json<{ id: string }>(
    await page.request.post("/api/v1/case-files", {
      headers,
      data: {
        incident_type: "traffic_collision",
        incident_date: "2026-10-01",
        description: `Refresco del editor E2E (${purpose}) ${token}`,
        declared_location: `Cruce del refresco ${token}`,
      },
    }),
    201,
  );
  const source = await uploadSource(page, caseFile.id, FIXTURE);
  const plan = await json<{ id: string; current_revision_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/plans`, {
      headers,
      data: { title: `Plano refresco ${token}`, primary_source_id: source.id, source_mode: "schematic_sketch" },
    }),
    201,
  );
  return { caseId: caseFile.id, planId: plan.id, revisionId: plan.current_revision_id };
}

/** Accept a `scan_segment` calibration in the wizard; it opens the editor (SPA navigation). */
async function calibrateInWizard(page: Page, seeded: Seeded, segmentMm: string): Promise<void> {
  const base = `/cases/${seeded.caseId}/plans/${seeded.planId}`;
  await page.goto(`${base}/calibration`);
  await expect(page.getByRole("heading", { level: 2, name: "Calibración" })).toBeVisible();
  await page.getByLabel("Método").selectOption({ label: "Escaneo con segmento" });
  const image = page.getByTestId("source-viewer").getByRole("img");
  await expect(image).toBeVisible();
  const box = await image.boundingBox();
  if (box === null) throw new Error("the source image has no box");
  await image.click({ position: { x: box.width * 0.1, y: box.height * 0.1 } });
  await image.click({ position: { x: box.width * 0.6, y: box.height * 0.1 } });
  await expect(page.getByText("Puntos marcados: 2 de 2.")).toBeVisible();
  await page.getByLabel("Longitud del segmento en el papel (mm)").fill(segmentMm);
  // A full page load clears this marker; the SPA navigation to the editor keeps it.
  await page.evaluate(() => {
    (globalThis as unknown as { __noReload?: boolean }).__noReload = true;
  });
  await page.getByRole("button", { name: "Continuar como aproximado" }).click();
  await page.waitForURL(`**${base}/editor`);
  await expect(page.getByTestId("editor-canvas")).toBeVisible();
}

const stillSamePage = (page: Page) =>
  page.evaluate(() => (globalThis as unknown as { __noReload?: boolean }).__noReload === true);

test.beforeEach(async ({ page }) => {
  await logIn(page);
});

test("the editor shows the chained suggestions without a reload", async ({ page }) => {
  test.setTimeout(JOBS_TIMEOUT_MS + 60_000);
  const seeded = await seedPlan(page, "sugerencias");
  // The editor's first read of the entities: if it already has the suggestions, the jobs beat
  // the editor and this run does not exercise the refresh (recorded as an annotation).
  const firstLoad = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().endsWith(`/api/v1/revisions/${seeded.revisionId}/entities`),
  );

  await calibrateInWizard(page, seeded, "100");

  const initial = await json<{ entities: { review_status: string }[] }>(await firstLoad, 200);
  const initialSuggestions = initial.entities.filter((e) => e.review_status === "suggested").length;
  test.info().annotations.push({
    type: "initial suggestions",
    description: `${String(initialSuggestions)} when the editor first loaded the entities`,
  });
  const toggle = page.getByRole("button", { name: /^Sugerencias \(\d+\)$/ });
  await expect(toggle).toBeVisible();
  await expect
    .poll(async () => Number(/\((\d+)\)/.exec((await toggle.textContent()) ?? "")?.[1] ?? 0), {
      timeout: JOBS_TIMEOUT_MS,
      message: "the editor shows the suggestions of the chained job",
    })
    .toBeGreaterThan(0);
  expect(await stillSamePage(page)).toBe(true);
  expect(page.url()).toContain(`/plans/${seeded.planId}/editor`);
  // When initialSuggestions is 0 the editor opened before the suggestions existed, so only its
  // own refresh explains N > 0. The progress notice is gone once the jobs finished.
  await expect(page.getByRole("status").filter({ hasText: /^(Buscando sugerencias|Generando)/ })).toHaveCount(0);
  await expect(page.getByText(RECTIFY_FAILED)).toHaveCount(0);
});

test("a failed rectification shows its cause, action and a way back to calibration", async ({ page }) => {
  test.setTimeout(JOBS_TIMEOUT_MS + 60_000);
  const seeded = await seedPlan(page, "rectificación fallida");

  await calibrateInWizard(page, seeded, "1000");

  const failure = page.getByRole("alert").filter({ hasText: RECTIFY_FAILED });
  await expect(failure).toBeVisible({ timeout: JOBS_TIMEOUT_MS });
  await expect(failure).toContainText(TOO_LARGE_CAUSE);
  await expect(failure).toContainText(TOO_LARGE_ACTION);
  expect(await stillSamePage(page)).toBe(true);
  // The suggestions count stays at zero, but the failure says why.
  await expect(page.getByRole("button", { name: "Sugerencias (0)", exact: true })).toBeVisible();

  await failure.getByRole("link", { name: "Volver a calibrar" }).click();
  await page.waitForURL(`**/cases/${seeded.caseId}/plans/${seeded.planId}/calibration`);
  await expect(page.getByRole("heading", { level: 2, name: "Calibración" })).toBeVisible();
});
