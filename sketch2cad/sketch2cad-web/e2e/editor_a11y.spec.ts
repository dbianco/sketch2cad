import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIResponse, type Page } from "@playwright/test";
import { rejectChainedSuggestions } from "./support";

// Keyboard-only drawing (jotdown H2-14) and the axe audit (spec 5, WCAG 2.1 AA) of the editor,
// calibration and review pages, against the compose stack. Each test seeds its own case, source
// and plan through the API with the page's session (cookie plus the X-CSRF-Token header).

const FIXTURE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
// Generous bound for the worker to pick the QC job up on a cold stack.
const QC_TIMEOUT_MS = 60_000;
const SYNC_TIMEOUT_MS = 15_000;
/** Tags of the WCAG 2.1 A and AA rules; best-practice rules are not part of the criterion. */
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const BLOCKING_IMPACTS = new Set(["critical", "serious"]);
/** Upper bound of Tab presses needed to reach the canvas from the top of the editor page. */
const MAX_TABS = 80;
/** Arrow presses between the two ends of the line: 20 grid steps of 0.1 m (default) = 2 m. */
const LINE_STEPS = 20;

interface Credentials {
  username: string;
  password: string;
}

interface Seeded {
  caseId: string;
  planId: string;
  revisionId: string;
}

/** Credentials of the user created by scripts/e2e-stack.sh (username and password lines). */
function readCredentials(): Credentials {
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

/** Log in with the keyboard only: fill the fields and submit with Enter. */
async function logIn(page: Page): Promise<void> {
  const { username, password } = readCredentials();
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(username);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByLabel("Contraseña").press("Enter");
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

/** Case, uploaded fixture (QC finished) and a plan in CALIBRATION_PENDING. */
async function seedPlan(page: Page, token: string): Promise<Seeded> {
  const headers = await csrfHeaders(page);
  const caseFile = await json<{ id: string }>(
    await page.request.post("/api/v1/case-files", {
      headers,
      data: {
        incident_type: "traffic_collision",
        incident_date: "2026-10-01",
        description: `Accesibilidad E2E ${token}`,
        declared_location: `Cruce de accesibilidad ${token}`,
      },
    }),
    201,
  );
  const bytes = readFileSync(FIXTURE);
  const source = await json<{ id: string; qc_job_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/sources`, {
      headers,
      multipart: {
        file: { name: path.basename(FIXTURE), mimeType: "image/png", buffer: bytes },
        client_sha256: createHash("sha256").update(bytes).digest("hex"),
      },
    }),
    201,
  );
  await expect
    .poll(
      async () => {
        const job = await json<{ status: string }>(
          await page.request.get(`/api/v1/jobs/${source.qc_job_id}`),
          200,
        );
        return job.status;
      },
      { timeout: QC_TIMEOUT_MS },
    )
    .toBe("succeeded");
  const plan = await json<{ id: string; status: string; current_revision_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/plans`, {
      headers,
      data: { title: `Plano ${token}`, primary_source_id: source.id, source_mode: "schematic_sketch" },
    }),
    201,
  );
  expect(plan.status).toBe("CALIBRATION_PENDING");
  return { caseId: caseFile.id, planId: plan.id, revisionId: plan.current_revision_id };
}

/**
 * Accept a deterministic calibration so that the plan reaches EDITING. The fixture PNG has no
 * DPI, so the method is `scan_segment`: two points 1000 px apart that span 100 mm of paper.
 */
async function calibrate(page: Page, { planId, revisionId }: Seeded): Promise<void> {
  const headers = await csrfHeaders(page);
  await json(await page.request.post(`/api/v1/plans/${planId}/lease`, { headers }), 200);
  const plan = await json<{ status: string }>(
    await page.request.post(`/api/v1/plans/${planId}/calibration/accept`, {
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
  expect(plan.status).toBe("EDITING");
  // Not about AI assistance: drop the suggestions chained after the calibration.
  await rejectChainedSuggestions(page, revisionId);
}

function token(): string {
  return `${Date.now().toString(36)}-${String(test.info().workerIndex)}`;
}

/** Run axe with the WCAG 2.1 A/AA rules; fail on critical or serious, log the rest. */
async function auditPage(page: Page, name: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  for (const violation of results.violations) {
    if (BLOCKING_IMPACTS.has(violation.impact ?? "")) continue;
    console.log(
      `[axe ${name}] ${violation.impact ?? "unknown"} ${violation.id}: ${violation.help} ` +
        `(${String(violation.nodes.length)} nodes)`,
    );
  }
  const blocking = results.violations
    .filter((violation) => BLOCKING_IMPACTS.has(violation.impact ?? ""))
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      targets: violation.nodes.map((node) => node.target.join(" ")),
    }));
  expect.soft(blocking, `${name}: critical or serious axe violations`).toEqual([]);
}

test("a line is created on the editor with the keyboard only", async ({ page }) => {
  await logIn(page);
  const seeded = await seedPlan(page, token());
  await calibrate(page, seeded);

  await page.goto(`/cases/${seeded.caseId}/plans/${seeded.planId}/editor`);
  const canvas = page.getByTestId("editor-canvas");
  await expect(canvas).toBeVisible();

  // Reach the canvas with Tab, as a keyboard user would.
  const focusedCanvas = page.locator("[data-testid='editor-canvas']:focus");
  const onCanvas = async () => (await focusedCanvas.count()) > 0;
  let tabs = 0;
  while (!(await onCanvas())) {
    tabs += 1;
    expect(tabs, "the canvas is reachable with Tab").toBeLessThanOrEqual(MAX_TABS);
    await page.keyboard.press("Tab");
  }

  await page.keyboard.press("l");
  await expect(page.getByRole("button", { name: "Línea", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.keyboard.press("Enter");
  for (let i = 0; i < LINE_STEPS; i += 1) await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("keyboard-cursor")).toBeVisible();
  await page.keyboard.press("Enter");

  const lines = async () => {
    const body = await json<{ entities: { type: string; deleted_at: string | null }[] }>(
      await page.request.get(`/api/v1/revisions/${seeded.revisionId}/entities`),
      200,
    );
    return body.entities.filter((entity) => entity.deleted_at === null).map((entity) => entity.type);
  };
  await expect.poll(lines, { timeout: SYNC_TIMEOUT_MS }).toEqual(["line"]);
  await expect(page.getByRole("status").filter({ hasText: "Sincronizado" })).toBeVisible();
});

test("the calibration, editor and review pages have no critical or serious axe violations", async ({
  page,
}) => {
  await logIn(page);
  const seeded = await seedPlan(page, token());
  const base = `/cases/${seeded.caseId}/plans/${seeded.planId}`;

  await page.goto(`${base}/calibration`);
  await expect(page.getByRole("heading", { level: 2, name: "Calibración" })).toBeVisible();
  await expect(page.getByTestId("source-viewer").getByRole("img")).toBeVisible();
  await auditPage(page, "calibration");

  await calibrate(page, seeded);
  await page.goto(`${base}/editor`);
  await expect(page.getByTestId("editor-canvas")).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeAttached();
  await auditPage(page, "editor");

  await page.getByRole("button", { name: "Ayuda de teclado" }).click();
  await expect(page.getByRole("dialog", { name: "Atajos de teclado" })).toBeVisible();
  await auditPage(page, "editor keyboard help");
  await page.keyboard.press("Escape");

  await page.goto(`${base}/review`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await auditPage(page, "review");
});
