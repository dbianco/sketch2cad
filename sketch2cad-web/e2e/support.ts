import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type APIResponse, type Page } from "@playwright/test";

// Plumbing shared by the E2E specs: the users written by scripts/e2e-stack.sh, login, the
// CSRF header, JSON responses, the upload of a fixture through the API and the axe audit.
// It holds no tests (Playwright only collects *.spec.ts). The older specs still carry their own
// copies of these helpers.

/** Generous bound for the worker to pick a job up on a cold stack. */
export const QC_TIMEOUT_MS = 60_000;
/** Tags of the WCAG 2.1 A and AA rules; best-practice rules are not part of the criterion. */
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const BLOCKING_IMPACTS = new Set(["critical", "serious"]);

export interface Credentials {
  username: string;
  password: string;
}

/**
 * Credentials of an E2E user: `credentials` (technician, E2E_CREDENTIALS_FILE),
 * `reviewer_credentials` or `admin_credentials`, files next to it with username and password.
 */
export function readCredentials(
  name: "credentials" | "reviewer_credentials" | "admin_credentials" = "credentials",
): Credentials {
  const technician = process.env.E2E_CREDENTIALS_FILE;
  if (technician === undefined) {
    throw new Error("E2E_CREDENTIALS_FILE is not set; run the suite through playwright.config.ts");
  }
  const file = name === "credentials" ? technician : path.join(path.dirname(technician), name);
  const [username = "", password = ""] = readFileSync(file, "utf8").split("\n");
  if (username === "" || password === "") throw new Error(`the E2E file ${name} is incomplete`);
  return { username, password };
}

/** Log the technician in through the login page. */
export async function logIn(page: Page): Promise<void> {
  const { username, password } = readCredentials();
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(username);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Expedientes" })).toBeVisible();
}

/** X-CSRF-Token header of a session: from a page's cookies or a request context's state. */
export async function csrfHeaders(session: Page | APIRequestContext): Promise<Record<string, string>> {
  const cookies =
    "context" in session ? await session.context().cookies() : (await session.storageState()).cookies;
  const cookie = cookies.find((c) => c.name === "s2c_csrf");
  if (cookie === undefined) throw new Error("no s2c_csrf cookie after login");
  return { "X-CSRF-Token": cookie.value };
}

/** Check the status (the body is the failure message) and parse the JSON body. */
export async function json<T>(
  response: Pick<APIResponse, "status" | "text" | "json">,
  status: number,
): Promise<T> {
  expect(response.status(), await response.text()).toBe(status);
  return (await response.json()) as T;
}

/** A token that keeps names unique across runs on the same stack. */
export function uniqueToken(): string {
  return `${Date.now().toString(36)}-${String(test.info().workerIndex)}`;
}

/** Upload `fixture` (PNG) to the case through the API and wait for its QC job to succeed. */
export async function uploadSource(
  page: Page,
  caseId: string,
  fixture: string,
): Promise<{ id: string }> {
  const bytes = readFileSync(fixture);
  const source = await json<{ id: string; qc_job_id: string }>(
    await page.request.post(`/api/v1/case-files/${caseId}/sources`, {
      headers: await csrfHeaders(page),
      multipart: {
        file: { name: path.basename(fixture), mimeType: "image/png", buffer: bytes },
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
      { timeout: QC_TIMEOUT_MS, message: "QC job of the upload" },
    )
    .toBe("succeeded");
  return source;
}

/** suggest_lines runs after rectify, which runs after the calibration: three worker jobs. */
const SUGGESTIONS_TIMEOUT_MS = 120_000;

/**
 * Wait for the suggestions that the calibration chained (`suggest_lines`) and reject them all.
 *
 * For specs that are not about AI assistance: with `ai_assist.enabled` on (the default) every
 * accepted calibration queues `suggest_lines`, whose pending suggestions keep the plan
 * approximate and add a `suggestions.added` event at an unpredictable moment. Rejecting them
 * soft-deletes them, so the plan is as if no suggestion had been made. Returns their ids; the
 * fixture must yield at least one suggestion (fixtures/synthetic_sketch.png yields 26).
 */
export async function rejectChainedSuggestions(page: Page, revisionId: string): Promise<string[]> {
  const pending = async () => {
    const body = await json<{ entities: { id: string; review_status: string }[] }>(
      await page.request.get(`/api/v1/revisions/${revisionId}/entities`),
      200,
    );
    return body.entities.filter((entity) => entity.review_status === "suggested").map((e) => e.id);
  };
  await expect
    .poll(async () => (await pending()).length, {
      timeout: SUGGESTIONS_TIMEOUT_MS,
      message: "suggest_lines inserted suggestions after the calibration",
    })
    .toBeGreaterThan(0);
  const ids = await pending();
  await json(
    await page.request.post(`/api/v1/revisions/${revisionId}/entities:bulk-review`, {
      headers: await csrfHeaders(page),
      data: { ids, review_status: "rejected" },
    }),
    200,
  );
  return ids;
}

export interface EventOut {
  kind: string;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

/** Every event of a plan, following `next_cursor` through all pages. */
export async function planEvents(page: Page, caseId: string, planId: string): Promise<EventOut[]> {
  const events: EventOut[] = [];
  let cursor: string | null = null;
  do {
    const params: Record<string, string> = { plan_id: planId };
    if (cursor !== null) params.cursor = cursor;
    const body: { items: EventOut[]; next_cursor: string | null } = await json(
      await page.request.get(`/api/v1/case-files/${caseId}/events`, { params }),
      200,
    );
    events.push(...body.items);
    cursor = body.next_cursor;
  } while (cursor !== null);
  return events;
}

interface SvgCanvas {
  getScreenCTM(): { a: number; b: number; c: number; d: number; e: number; f: number } | null;
  getAttribute(name: string): string | null;
  parentElement: { clientWidth: number; clientHeight: number };
}

/** Client position of a world point through the editor canvas's screen CTM, once its size settled. */
export async function worldToClient(page: Page, world: [number, number]): Promise<{ x: number; y: number }> {
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

/** Run axe with the WCAG 2.1 A/AA rules; fail (soft) on critical or serious, log the rest. */
export async function auditPage(page: Page, name: string): Promise<void> {
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
