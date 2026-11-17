import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Deterministic sharp synthetic sketch (2000 x 2828 px, 8-bit gray PNG) drawn with
// sketch2cad.qc.synthetic.sketch(2000): every quality check is green.
const FIXTURE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
const FIXTURE_NAME = path.basename(FIXTURE);
const SHA256_HEX = /^[0-9a-f]{64}$/;
// Generous bound for the worker to pick the QC job up on a cold stack.
const QC_TIMEOUT_MS = 60_000;

interface Credentials {
  username: string;
  password: string;
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

function sha256Hex(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

async function logIn(page: Page, { username, password }: Credentials): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(username);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Expedientes" })).toBeVisible();
}

/** Create a case through the UI and return the link of its code in the list. */
async function createCase(page: Page, location: string) {
  await page.getByRole("button", { name: "Nuevo expediente" }).click();
  const form = page.getByRole("form", { name: "Nuevo expediente" });
  await form.getByLabel("Tipo de hecho").selectOption("traffic_collision");
  await form.getByLabel("Fecha del hecho").fill("2026-10-01");
  await form.getByLabel("Descripción").fill(`Ingesta E2E ${location}`);
  await form.getByLabel("Ubicación declarada").fill(location);
  await form.getByRole("button", { name: "Crear expediente" }).click();
  await expect(form).toBeHidden();
  const row = page.getByRole("row").filter({ hasText: location });
  await expect(row).toHaveCount(1);
  return row.getByRole("rowheader").getByRole("link");
}

test("a technician uploads a sketch, sees a green QC and the preview, and the original keeps its hash", async ({
  page,
}) => {
  const fixtureSha256 = sha256Hex(readFileSync(FIXTURE));
  const token = `${Date.now().toString(36)}-${test.info().workerIndex}`;

  await logIn(page, readCredentials());
  const caseLink = await createCase(page, `Cruce de ingesta ${token}`);
  await caseLink.click();
  await expect(page).toHaveURL(/\/cases\/[^/]+\/upload$/);
  await expect(page.getByRole("heading", { level: 1, name: "Cargar el croquis" })).toBeVisible();

  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Elegir archivo" }).click();
  await (await chooser).setFiles(FIXTURE);

  // Server hash equals the client hash, and the full SHA-256 is displayed.
  await expect(page.getByText("Hash verificado. Original guardado sin cambios.")).toBeVisible();
  const displayed = page.getByTestId("source-sha256");
  await expect(displayed).toHaveText(SHA256_HEX);
  const displayedSha256 = ((await displayed.textContent()) ?? "").trim();

  // QC traffic light: the worker runs the job and every check of the fixture is green.
  await expect(page.getByText("Resultado: Verde")).toBeVisible({ timeout: QC_TIMEOUT_MS });
  const checks = page.getByRole("list", { name: "Controles de calidad" }).getByRole("listitem");
  await expect(checks).toHaveCount(4);
  await expect(checks.filter({ hasText: "● Verde" })).toHaveCount(4);
  await expect(page.getByRole("button", { name: "Continuar a calibración" })).toBeEnabled();

  // Viewer: the derived preview is loaded and decoded.
  const viewer = page.getByRole("region", { name: `Visor de la fuente: ${FIXTURE_NAME}` });
  const preview = viewer.getByRole("img", { name: FIXTURE_NAME });
  await expect(preview).toBeVisible();
  await expect(preview).toHaveJSProperty("complete", true);
  await expect(preview).not.toHaveJSProperty("naturalWidth", 0);
  await viewer.getByRole("button", { name: "Acercar" }).click();
  await expect(viewer).not.toHaveAttribute("data-scale", "1");

  // Download the original through the UI link (browser session cookie).
  const link = page.getByRole("link", { name: "Descargar el original" });
  const downloadEvent = page.waitForEvent("download");
  await link.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe(FIXTURE_NAME);
  const downloadedSha256 = sha256Hex(readFileSync(await download.path()));

  // And through the page's authenticated request context, checking the integrity header.
  const href = (await link.getAttribute("href")) ?? "";
  expect(href).toMatch(/^\/api\/v1\/sources\/[^/]+\/original$/);
  const response = await page.request.get(href);
  expect(response.status()).toBe(200);
  const requestedSha256 = sha256Hex(await response.body());

  expect(displayedSha256).toBe(fixtureSha256);
  expect(downloadedSha256).toBe(displayedSha256);
  expect(requestedSha256).toBe(displayedSha256);
  expect(response.headers()["x-content-sha256"]).toBe(displayedSha256);
});
