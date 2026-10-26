import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { crc32 } from "node:zlib";
import { expect, test, type APIResponse, type Page } from "@playwright/test";

// Validate, export and verify the package against the compose stack (jotdown H4-10, CA-09,
// US-16, US-17, REQ-14). The technician seeds a calibrated plan through the API, adds the
// reviewer user (created by scripts/e2e-stack.sh) to the case file with read, edit and deliver,
// and sends the plan to review from the review page. The reviewer, in a second browser context,
// takes the review and validates it, opens the export page, runs the final export, downloads the
// ZIP through the confirmation dialog (which must list the hashes) and the test runs the offline
// verifier on the file with the stack's public key id pinned: exit 0. One flipped byte inside
// plan.dxf must then make the verifier exit 1 naming plan.dxf, both with the ZIP CRC left stale
// (step 1, unreadable member) and with the CRC rewritten (step 4, SHA-256 mismatch).
//
// Prerequisite: `uv` on PATH; scripts/verify_downloaded_zip.sh runs the verifier with the
// locked environment of sketch2cad-api/ (the first run syncs it).

const IMAGE = path.join(import.meta.dirname, "fixtures", "synthetic_sketch.png");
const VERIFY_SCRIPT = path.join(import.meta.dirname, "..", "scripts", "verify_downloaded_zip.sh");
// Generous bounds for the worker on a cold stack (QC job, then the package build).
const QC_TIMEOUT_MS = 60_000;
const EXPORT_TIMEOUT_MS = 120_000;
/** The first verifier run may sync the API's virtual environment. */
const VERIFY_TIMEOUT_MS = 180_000;
/** Members of the package that the manifest does not hash (sketch2cad_verify.UNHASHED_MEMBERS). */
const UNHASHED = new Set(["manifest.json", "VERIFY.md", "public_key.pem"]);
const TAMPERED_MEMBER = "plan.dxf";

type Point2 = [number, number];

interface Credentials {
  username: string;
  password: string;
}

interface Seeded {
  caseId: string;
  planId: string;
}

interface ZipMember {
  name: string;
  /** Offset of the member's central directory record. */
  centralOffset: number;
  /** Offset of the member's local file header. */
  localOffset: number;
  dataOffset: number;
  size: number;
  method: number;
}

/** Directory of the files written by scripts/e2e-stack.sh (next to E2E_CREDENTIALS_FILE). */
function stateFile(name: string): string {
  const file = process.env.E2E_CREDENTIALS_FILE;
  if (file === undefined) {
    throw new Error("E2E_CREDENTIALS_FILE is not set; run the suite through playwright.config.ts");
  }
  return name === "credentials" ? file : path.join(path.dirname(file), name);
}

function readCredentials(name: "credentials" | "reviewer_credentials"): Credentials {
  const [username = "", password = ""] = readFileSync(stateFile(name), "utf8").split("\n");
  if (username === "" || password === "") {
    throw new Error(`the E2E ${name} file is incomplete`);
  }
  return { username, password };
}

async function logIn(page: Page, credentials: Credentials): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(credentials.username);
  await page.getByLabel("Contraseña").fill(credentials.password);
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
  return { type: "line", layer: "L_STRUCTURE", geometry: { kind: "line", coords_m: coords }, attributes: {} };
}

/** Case, image (QC done), plan, lease, scan_segment calibration (EDITING) and a few entities. */
async function seed(page: Page): Promise<Seeded> {
  const token = Date.now().toString(36);
  const headers = await csrfHeaders(page);
  const caseFile = await json<{ id: string }>(
    await page.request.post("/api/v1/case-files", {
      headers,
      data: {
        incident_type: "traffic_collision",
        incident_date: "2026-10-01",
        description: `Exportación E2E ${token}`,
        declared_location: "Cruce de la exportación",
      },
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
      data: { title: `Plano exportado ${token}`, source_mode: "schematic_sketch", primary_source_id: source.id },
    }),
    201,
  );
  await json(await page.request.post(`/api/v1/plans/${plan.id}/lease`, { headers }), 200);
  // 1000 px of the synthetic sketch span 100 mm of paper at the provisional 1:100.
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
  const evidence = await json<{ id: string }>(
    await page.request.post(`/api/v1/case-files/${caseFile.id}/evidence`, {
      headers,
      data: { label: "Casquillo" },
    }),
    201,
  );
  const entities = [
    line([[2, -20], [12, -20]]),
    line([[2, -4], [10, -4]]),
    {
      type: "text",
      layer: "L_TEXT",
      geometry: { kind: "text", anchor_m: [14, -9], height_mm_paper: 3.5, content: "Calzada" },
      attributes: {},
    },
    {
      type: "symbol",
      layer: "L_EVIDENCE",
      geometry: {
        kind: "symbol",
        anchor_m: [14, -14],
        symbol_id: "evidence_marker",
        attributes: { evidence_number: 1 },
      },
      evidence_id: evidence.id,
    },
  ];
  await json(
    await page.request.post(`/api/v1/revisions/${plan.current_revision_id}/entities`, {
      headers,
      data: { entities },
    }),
    201,
  );
  return { caseId: caseFile.id, planId: plan.id };
}

async function planStatus(page: Page, planId: string): Promise<string> {
  return (await json<{ status: string }>(await page.request.get(`/api/v1/plans/${planId}`), 200)).status;
}

/** Click a transition button of the review page and wait until the API reports `status`. */
async function transitionThroughUi(page: Page, planId: string, button: string, status: string): Promise<void> {
  const decision = page.getByRole("region", { name: "Decisión" });
  await decision.getByRole("button", { name: button, exact: true }).click();
  await expect.poll(() => planStatus(page, planId)).toBe(status);
}

/** Members of a ZIP read from its central directory (enough for the package's stored members). */
function zipMembers(zip: Buffer): ZipMember[] {
  const eocd = zip.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  if (eocd < 0) throw new Error("no end of central directory record");
  const count = zip.readUInt16LE(eocd + 10);
  let offset = zip.readUInt32LE(eocd + 16);
  const members: ZipMember[] = [];
  for (let index = 0; index < count; index += 1) {
    if (zip.readUInt32LE(offset) !== 0x02014b50) throw new Error("bad central directory record");
    const nameLength = zip.readUInt16LE(offset + 28);
    const recordLength = 46 + nameLength + zip.readUInt16LE(offset + 30) + zip.readUInt16LE(offset + 32);
    const localOffset = zip.readUInt32LE(offset + 42);
    const localExtra = 30 + zip.readUInt16LE(localOffset + 26) + zip.readUInt16LE(localOffset + 28);
    members.push({
      name: zip.toString("utf8", offset + 46, offset + 46 + nameLength),
      centralOffset: offset,
      localOffset,
      dataOffset: localOffset + localExtra,
      size: zip.readUInt32LE(offset + 20),
      method: zip.readUInt16LE(offset + 10),
    });
    offset += recordLength;
  }
  return members;
}

function memberData(zip: Buffer, member: ZipMember): Buffer {
  expect(member.method, `${member.name} must be stored (not compressed)`).toBe(0);
  return zip.subarray(member.dataOffset, member.dataOffset + member.size);
}

/** Copy of `zip` with one byte inverted in the middle of `name`'s data; optionally fix its CRC. */
function flipOneByte(zip: Buffer, name: string, fixCrc: boolean): Buffer {
  const copy = Buffer.from(zip);
  const member = zipMembers(copy).find((candidate) => candidate.name === name);
  if (member === undefined) throw new Error(`${name} is not in the package`);
  const position = member.dataOffset + Math.floor(member.size / 2);
  copy[position] = (copy[position] ?? 0) ^ 0xff;
  if (fixCrc) {
    const crc = crc32(memberData(copy, member));
    copy.writeUInt32LE(crc, member.localOffset + 14);
    copy.writeUInt32LE(crc, member.centralOffset + 16);
  }
  return copy;
}

function runVerifier(zipPath: string, keyId: string): { status: number | null; stdout: string; stderr: string } {
  const result = spawnSync("bash", [VERIFY_SCRIPT, zipPath, "--key-id", keyId], {
    encoding: "utf8",
    timeout: VERIFY_TIMEOUT_MS,
  });
  if (result.error !== undefined) throw result.error;
  void test.info().attach(`verifier ${path.basename(zipPath)}`, {
    body: `exit ${String(result.status)}\n${result.stdout}${result.stderr}`,
    contentType: "text/plain",
  });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

test.use({ viewport: { width: 1600, height: 1000 } });

test("reviewer validates, exports the final package, downloads it and the verifier accepts it", async ({
  page,
  browser,
}) => {
  test.setTimeout(QC_TIMEOUT_MS + EXPORT_TIMEOUT_MS + 2 * VERIFY_TIMEOUT_MS);
  const keyId = readFileSync(stateFile("signing_key_id"), "utf8").trim();
  expect(keyId).toMatch(/^ed25519:[0-9a-f]{16}$/);

  await logIn(page, readCredentials("credentials"));
  const { caseId, planId } = await seed(page);

  const reviewerContext = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const workDir = mkdtempSync(path.join(os.tmpdir(), "sketch2cad-export-"));
  try {
    const reviewer = await reviewerContext.newPage();
    await logIn(reviewer, readCredentials("reviewer_credentials"));
    const me = await json<{ user: { id: string; role: string } }>(await reviewer.request.get("/api/v1/auth/me"), 200);
    expect(me.user.role).toBe("reviewer");
    // The creator holds deliver; the reviewer needs edit (transitions) and deliver (final export).
    await json(
      await page.request.post(`/api/v1/case-files/${caseId}/members`, {
        headers: await csrfHeaders(page),
        data: { user_id: me.user.id, permissions: ["read", "edit", "deliver"] },
      }),
      201,
    );

    const reviewPath = `/cases/${caseId}/plans/${planId}/review`;
    await page.goto(reviewPath);
    await transitionThroughUi(page, planId, "Enviar a revisión", "REVIEW_PENDING");

    await reviewer.goto(reviewPath);
    await transitionThroughUi(reviewer, planId, "Tomar revisión", "IN_REVIEW");
    await transitionThroughUi(reviewer, planId, "Validar", "VALIDATED");

    await reviewer.goto(reviewPath);
    await reviewer.getByRole("link", { name: "Exportar", exact: true }).click();
    await expect(reviewer).toHaveURL(new RegExp(`/cases/${caseId}/plans/${planId}/export$`));
    await reviewer.getByRole("button", { name: "Generar exportación final" }).click();
    await expect(reviewer.getByText("Exportación completada.")).toBeVisible({ timeout: EXPORT_TIMEOUT_MS });

    const artifacts = await json<{ kind: string; filename: string; sha256: string; is_draft: boolean }[]>(
      await reviewer.request.get(`/api/v1/plans/${planId}/artifacts`),
      200,
    );
    const zipArtifact = artifacts.find((artifact) => artifact.kind === "zip" && !artifact.is_draft);
    if (zipArtifact === undefined) throw new Error("the final export produced no ZIP artifact");
    expect(await planStatus(reviewer, planId)).toBe("EXPORTED");

    await reviewer.getByRole("button", { name: `Descargar ${zipArtifact.filename}` }).click();
    const dialog = reviewer.getByRole("dialog", { name: "Confirmar la descarga" });
    const packageList = dialog.getByRole("list", { name: "Archivos del paquete firmado" });
    await expect(packageList).toBeVisible();
    await expect(dialog.getByRole("list", { name: "Archivo a descargar" })).toContainText(zipArtifact.sha256);
    const [download] = await Promise.all([
      reviewer.waitForEvent("download"),
      dialog.getByRole("button", { name: "Confirmar y descargar" }).click(),
    ]);
    await expect(dialog.getByText("Descarga completada. Hash verificado.")).toBeVisible();
    expect(download.suggestedFilename()).toBe(zipArtifact.filename);
    const zipPath = path.join(workDir, zipArtifact.filename);
    await download.saveAs(zipPath);
    const zip = readFileSync(zipPath);
    expect(createHash("sha256").update(zip).digest("hex")).toBe(zipArtifact.sha256);

    // The dialog listed the SHA-256 of every hashed member of the package that was downloaded.
    const hashed = zipMembers(zip).filter((member) => !UNHASHED.has(member.name));
    expect(hashed.map((member) => member.name)).toContain(TAMPERED_MEMBER);
    for (const member of hashed) {
      const sha = createHash("sha256").update(memberData(zip, member)).digest("hex");
      await expect(packageList).toContainText(`${member.name}: ${sha}`);
    }

    const intact = runVerifier(zipPath, keyId);
    expect(intact.status, intact.stdout + intact.stderr).toBe(0);
    expect(intact.stdout).toContain("RESULTADO: OK");
    expect(intact.stdout).toContain(`clave ${keyId}`);
    expect(intact.stdout).toContain("identificador fijado con --key-id");
    // Pinning is effective: another key id makes the signature check fail.
    const otherKeyId = keyId === "ed25519:0000000000000000" ? "ed25519:1111111111111111" : "ed25519:0000000000000000";
    const otherKey = runVerifier(zipPath, otherKeyId);
    expect(otherKey.status, otherKey.stdout + otherKey.stderr).toBe(1);

    // Exactly one artifact.downloaded event: the confirmed download (the dialog's details request
    // without confirm records nothing).
    const downloads = await json<{ items: { kind: string; actor_id: string }[]; next_cursor: string | null }>(
      await reviewer.request.get(`/api/v1/case-files/${caseId}/events?kind=artifact.downloaded`),
      200,
    );
    expect(downloads.items.map((event) => [event.kind, event.actor_id])).toEqual([
      ["artifact.downloaded", me.user.id],
    ]);
    expect(await planStatus(reviewer, planId)).toBe("EXPORTED");

    // Stale CRC: the member is unreadable (check 1); rewritten CRC: its SHA-256 differs (check 4).
    const expectedFailure = {
      raw: `${TAMPERED_MEMBER}: no se puede leer`,
      crc: `${TAMPERED_MEMBER}: el SHA-256 no coincide`,
    };
    for (const fixCrc of [false, true]) {
      const tamperedPath = path.join(workDir, `tampered-${fixCrc ? "crc" : "raw"}.zip`);
      writeFileSync(tamperedPath, flipOneByte(zip, TAMPERED_MEMBER, fixCrc));
      const tampered = runVerifier(tamperedPath, keyId);
      expect(tampered.status, tampered.stdout + tampered.stderr).toBe(1);
      expect(tampered.stdout).toContain("RESULTADO: FALLO");
      expect(tampered.stdout).toContain(expectedFailure[fixCrc ? "crc" : "raw"]);
      expect(tampered.stdout).toMatch(new RegExp(`Afectados: .*${TAMPERED_MEMBER.replace(".", "\\.")}`));
    }
  } finally {
    await reviewerContext.close();
    rmSync(workDir, { recursive: true, force: true });
  }
});
