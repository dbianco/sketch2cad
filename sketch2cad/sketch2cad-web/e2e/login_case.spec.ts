import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const CASE_CODE = /^EXP-\d{4}-\d{4}$/;

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

async function logIn(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(username);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Entrar" }).click();
}

test("a technician logs in, creates a case and sees it after a reload", async ({ page }) => {
  const { username, password } = readCredentials();
  const token = `${Date.now().toString(36)}-${test.info().workerIndex}`;
  const description = `Colisión de prueba E2E ${token}`;
  const location = `Av. Siempre Viva 742 ${token}`;

  await logIn(page, username, password);
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1, name: "Expedientes" })).toBeVisible();

  await page.getByRole("button", { name: "Nuevo expediente" }).click();
  const form = page.getByRole("form", { name: "Nuevo expediente" });
  await form.getByLabel("Tipo de hecho").selectOption("traffic_collision");
  await form.getByLabel("Fecha del hecho").fill("2026-10-01");
  await form.getByLabel("Descripción").fill(description);
  await form.getByLabel("Ubicación declarada").fill(location);
  await form.getByRole("button", { name: "Crear expediente" }).click();
  await expect(form).toBeHidden();

  const row = page.getByRole("row").filter({ hasText: location });
  await expect(row).toHaveCount(1);
  const code = (await row.getByRole("rowheader").textContent())?.trim() ?? "";
  expect(code).toMatch(CASE_CODE);
  await expect(page.getByText(`Expediente ${code} creado.`)).toBeVisible();

  // The list does not show the description, so check the stored case through the API with
  // the same session cookie.
  const response = await page.request.get("/api/v1/case-files", { params: { q: code } });
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { items: { code: string; description: string }[] };
  expect(body.items).toContainEqual(expect.objectContaining({ code, description }));

  // The session cookie survives a reload and the case persists.
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Expedientes" })).toBeVisible();
  const persisted = page.getByRole("row").filter({ hasText: location });
  await expect(persisted.getByRole("rowheader")).toHaveText(code);

  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
});

test("a wrong password shows the invalid credentials message", async ({ page }) => {
  const { username } = readCredentials();

  await logIn(page, username, "not-the-password");

  await expect(page.getByRole("alert")).toHaveText("Usuario o contraseña incorrectos.");
  await expect(page).toHaveURL(/\/login$/);
});
