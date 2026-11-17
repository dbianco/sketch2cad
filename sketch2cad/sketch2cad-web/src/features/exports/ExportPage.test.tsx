import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes as RouterRoutes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlanOut } from "../../editor/plan/planStore";
import { authStore } from "../auth/authStore";
import { CASE_ID, PLAN_ID, REVISION_ID, fetchMock, jsonResponse, planOut, sent, signInAs, type Routes } from "../plans/testing";
import { sha256Hex } from "../sources/hashFile";
import { JOB_POLL_INTERVAL_MS } from "./ExportPage";
import { ExportPage } from "./ExportPage";
import { DISCLAIMER_TEXT, SIGNATURE_NOTICE, V11_MESSAGE, planExportPath, type ArtifactOut } from "./messages";

const PLAN_URL = `/api/v1/plans/${PLAN_ID}`;
const EXPORTS_URL = `${PLAN_URL}/exports`;
const ARTIFACTS_URL = `${PLAN_URL}/artifacts`;
const JOB_ID = "00000000-0000-4000-8000-0000000000f1";
const JOB_URL = `/api/v1/jobs/${JOB_ID}`;
const ZIP_ID = "00000000-0000-4000-8000-000000000201";
const DRAFT_ID = "00000000-0000-4000-8000-000000000202";

const ZIP_BYTES = new TextEncoder().encode("PK final package bytes");
const DRAFT_BYTES = new TextEncoder().encode("%PDF draft bytes");
const hex = (digit: string) => digit.repeat(64);

function artifact(overrides: Partial<ArtifactOut>): ArtifactOut {
  return {
    id: ZIP_ID,
    plan_id: PLAN_ID,
    revision_id: REVISION_ID,
    kind: "zip",
    is_draft: false,
    filename: "sketch2cad_EXP-1_plano1_r2.zip",
    media_type: "application/zip",
    sha256: sha256Hex(ZIP_BYTES),
    byte_size: 2048,
    generator_version: "0.1.0",
    created_at: "2026-10-26T10:00:00Z",
    ...overrides,
  };
}

const ZIP = artifact({});
const DRAFT = artifact({
  id: DRAFT_ID,
  kind: "pdf_plan",
  is_draft: true,
  filename: "plano1_borrador.pdf",
  media_type: "application/pdf",
  sha256: sha256Hex(DRAFT_BYTES),
});

const MANIFEST = {
  manifest_version: "1",
  revision: { number: 2, snapshot_sha256: hex("5") },
  artifacts: [
    { kind: "dxf", filename: "plan.dxf", sha256: hex("1") },
    { kind: "pdf_plan", filename: "plan.pdf", sha256: hex("2") },
    { kind: "pdf_audit", filename: "audit.pdf", sha256: hex("3") },
  ],
  events_chain: { count: 42, last_hash: hex("6") },
  signature: { scheme: "ed25519-jcs-v1", public_key_id: "ed25519:abcd1234", value: "c2ln" },
};

function job(status: "queued" | "running" | "succeeded" | "failed", error: { cause: string; action: string } | null = null) {
  return { id: JOB_ID, kind: "export", status, source_id: null, error, result_ref: null };
}

function exportRoutes(plan: PlanOut, overrides: Routes = {}): Routes {
  return {
    [`GET ${PLAN_URL}`]: jsonResponse(plan),
    [`GET ${ARTIFACTS_URL}`]: jsonResponse([ZIP, DRAFT]),
    [`GET /api/v1/artifacts/${ZIP_ID}`]: jsonResponse({ ...ZIP, manifest: MANIFEST }),
    [`GET /api/v1/artifacts/${DRAFT_ID}`]: jsonResponse({ ...DRAFT, manifest: null }),
    [`POST ${EXPORTS_URL}`]: jsonResponse(job("queued"), 202),
    ...overrides,
  };
}

/** Sign in, stub the API (bytes of `?confirm=true` by artifact id) and render the page. */
async function renderExport(plan: PlanOut, overrides: Routes = {}, role: "technician" | "reviewer" = "reviewer") {
  await signInAs(role, exportRoutes(plan, overrides));
  const routed = fetchMock.getMockImplementation();
  fetchMock.mockImplementation((input, init) => {
    const url = String(input);
    if (url.includes("confirm=true")) {
      const bytes = url.includes(ZIP_ID) ? ZIP_BYTES : DRAFT_BYTES;
      return Promise.resolve(new Response(bytes, { headers: { "Content-Type": "application/octet-stream" } }));
    }
    return routed === undefined ? Promise.reject(new Error("no stub")) : routed(input, init);
  });
  render(
    <MemoryRouter initialEntries={[planExportPath(CASE_ID, PLAN_ID)]}>
      <RouterRoutes>
        <Route path="/cases/:caseId/plans/:planId/export" element={<ExportPage />} />
      </RouterRoutes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: /Exportación · Plano 1/ });
}

const urls = () => fetchMock.mock.calls.map(([input]) => String(input));
const confirmedDownloads = () => urls().filter((url) => url.includes("confirm=true"));
const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

async function openDialog(filename: string) {
  fireEvent.click(button(`Descargar ${filename}`));
  const dialog = screen.getByRole("dialog", { name: "Confirmar la descarga" });
  await within(dialog).findByRole("button", { name: "Confirmar y descargar" });
  await vi.waitFor(() => expect(button("Confirmar y descargar").disabled).toBe(false));
  return dialog;
}

describe("ExportPage", () => {
  const click = vi.fn();
  beforeEach(() => {
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:artifact"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(click);
  });
  afterEach(() => {
    cleanup();
    authStore.reset();
    fetchMock.mockReset();
    click.mockReset();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("lists every artifact with its Spanish kind, a draft/final badge and the full SHA-256", async () => {
    await renderExport(planOut({ status: "VALIDATED" }));
    const rows = screen.getAllByRole("row").slice(1);

    expect(rows).toHaveLength(2);
    const [zipRow, draftRow] = rows.map((row) => within(row));
    expect(zipRow?.getByText("Paquete ZIP")).toBeTruthy();
    expect(zipRow?.getByText("Final")).toBeTruthy();
    expect(zipRow?.getByText(ZIP.sha256)).toBeTruthy();
    expect(zipRow?.getByText("2 KB")).toBeTruthy();
    expect(draftRow?.getByText("Plano PDF")).toBeTruthy();
    expect(draftRow?.getByText("Borrador")).toBeTruthy();
    expect(draftRow?.getByText(DRAFT.sha256)).toBeTruthy();
    expect(ZIP.sha256).toHaveLength(64);
  });

  it("downloads only after confirming the dialog that lists every hash", async () => {
    await renderExport(planOut({ status: "EXPORTED" }));
    const dialog = await openDialog(ZIP.filename);

    expect(urls()).toContain(`/api/v1/artifacts/${ZIP_ID}`);
    expect(confirmedDownloads()).toEqual([]);
    const packageFiles = within(within(dialog).getByRole("list", { name: "Archivos del paquete firmado" }));
    expect(packageFiles.getAllByRole("listitem")).toHaveLength(MANIFEST.artifacts.length);
    for (const file of MANIFEST.artifacts) {
      expect(packageFiles.getByText(file.sha256)).toBeTruthy();
      expect(packageFiles.getByText(new RegExp(file.filename))).toBeTruthy();
    }
    const view = within(dialog);
    expect(view.getByText(hex("5"))).toBeTruthy();
    expect(view.getByText(hex("6"))).toBeTruthy();
    expect(view.getByText(/ed25519-jcs-v1/)).toBeTruthy();
    expect(view.getByText("ed25519:abcd1234")).toBeTruthy();
    expect(view.getByText(ZIP.sha256)).toBeTruthy();
    expect(view.getByText(new RegExp(SIGNATURE_NOTICE))).toBeTruthy();
    expect(view.getByText(new RegExp(DISCLAIMER_TEXT))).toBeTruthy();

    fireEvent.click(button("Confirmar y descargar"));

    expect(await view.findByText("Descarga completada. Hash verificado.")).toBeTruthy();
    expect(confirmedDownloads()).toEqual([`/api/v1/artifacts/${ZIP_ID}?confirm=true`]);
    expect(click).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("sends no download request when the dialog is cancelled or closed with Esc", async () => {
    await renderExport(planOut({ status: "EXPORTED" }));

    await openDialog(ZIP.filename);
    fireEvent.click(button("Cancelar"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(button(`Descargar ${ZIP.filename}`));

    const dialog = await openDialog(DRAFT.filename);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(confirmedDownloads()).toEqual([]);
    expect(click).not.toHaveBeenCalled();
  });

  it("traps Tab inside the dialog", async () => {
    await renderExport(planOut({ status: "EXPORTED" }));
    const dialog = await openDialog(ZIP.filename);
    expect(document.activeElement).toBe(dialog);

    const confirm = button("Confirmar y descargar");
    const cancel = button("Cancelar");
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(confirm);
    cancel.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(confirm);
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(cancel);
  });

  it("shows a draft's own hash and that drafts carry no manifest", async () => {
    await renderExport(planOut({ status: "EDITING" }));
    const dialog = within(await openDialog(DRAFT.filename));

    expect(dialog.getByText(DRAFT.sha256)).toBeTruthy();
    expect(dialog.getByText(/Los borradores no llevan manifiesto firmado/)).toBeTruthy();
    fireEvent.click(button("Confirmar y descargar"));
    await dialog.findByText("Descarga completada. Hash verificado.");
    expect(confirmedDownloads()).toEqual([`/api/v1/artifacts/${DRAFT_ID}?confirm=true`]);
  });

  it("does not save a download whose hash differs from the listed one", async () => {
    const tampered = { ...DRAFT, sha256: hex("9") };
    await renderExport(planOut({ status: "EDITING" }), {
      [`GET ${ARTIFACTS_URL}`]: jsonResponse([tampered]),
      [`GET /api/v1/artifacts/${DRAFT_ID}`]: jsonResponse({ ...tampered, manifest: null }),
    });
    const dialog = within(await openDialog(DRAFT.filename));

    fireEvent.click(button("Confirmar y descargar"));

    expect(await dialog.findByText(/no coincide con el registrado/)).toBeTruthy();
    expect(click).not.toHaveBeenCalled();
  });

  it("disables the final export with the V-11 message for a plan in EDITING", async () => {
    await renderExport(planOut({ status: "EDITING" }));
    const final = button("Generar exportación final");

    expect(final.disabled).toBe(true);
    expect(screen.getByText(V11_MESSAGE)).toBeTruthy();
    expect(final.getAttribute("aria-describedby")).toBe(screen.getByText(V11_MESSAGE).id);
    expect(button("Generar borrador").disabled).toBe(false);
    expect(screen.getByText(DISCLAIMER_TEXT)).toBeTruthy();
  });

  it("enables the final export for a validated plan and sends the paper and scale chosen", async () => {
    await renderExport(planOut({ status: "VALIDATED" }), {}, "reviewer");
    const final = button("Generar exportación final");
    expect(final.disabled).toBe(false);
    expect(screen.queryByText(V11_MESSAGE)).toBeNull();

    fireEvent.change(screen.getByRole("combobox", { name: "Papel" }), { target: { value: "A3" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Escala" }), { target: { value: "200" } });
    fireEvent.click(final);

    await screen.findByText("Exportación en cola…");
    expect(sent("POST", EXPORTS_URL)).toEqual([{ mode: "final", paper: "A3", scale: 200 }]);
  });

  it("sends a draft with automatic paper and a null scale by default", async () => {
    await renderExport(planOut({ status: "EDITING" }));
    expect((screen.getByRole("combobox", { name: "Escala" }) as HTMLSelectElement).value).toBe("auto");
    expect(screen.getByRole("option", { name: "Automática — la más grande que cabe" })).toBeTruthy();

    fireEvent.click(button("Generar borrador"));

    await screen.findByText("Exportación en cola…");
    expect(sent("POST", EXPORTS_URL)).toEqual([{ mode: "draft", paper: "auto", scale: null }]);
  });

  it("polls the job every 2 s, stops at the terminal state and refreshes the artifact list", async () => {
    const statuses = [job("running"), job("succeeded")];
    let listed: ArtifactOut[] = [];
    await renderExport(planOut({ status: "EDITING" }), {
      [`GET ${ARTIFACTS_URL}`]: () => jsonResponse(listed),
      [`GET ${JOB_URL}`]: () => {
        const next = statuses.shift() ?? job("succeeded");
        if (next.status === "succeeded") listed = [DRAFT];
        return jsonResponse(next);
      },
    });
    expect(screen.getByText("Todavía no hay archivos exportados.")).toBeTruthy();
    vi.useFakeTimers();
    const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

    fireEvent.click(button("Generar borrador"));
    await advance(0);
    expect(sent("GET", JOB_URL)).toHaveLength(0);
    await advance(JOB_POLL_INTERVAL_MS - 1);
    expect(sent("GET", JOB_URL)).toHaveLength(0);
    await advance(1);
    expect(sent("GET", JOB_URL)).toHaveLength(1);
    expect(screen.getByText("Generando la exportación…")).toBeTruthy();
    await advance(JOB_POLL_INTERVAL_MS);
    expect(sent("GET", JOB_URL)).toHaveLength(2);
    await advance(JOB_POLL_INTERVAL_MS * 5);
    expect(sent("GET", JOB_URL)).toHaveLength(2);

    expect(screen.getByText("Exportación completada.")).toBeTruthy();
    expect(screen.getByText(DRAFT.filename)).toBeTruthy();
  });

  it("shows the Spanish cause and action of a failed job", async () => {
    const error = { cause: "El plano no tiene entidades.", action: "Dibuja el plano y vuelve a exportar." };
    await renderExport(planOut({ status: "EDITING" }), {
      [`POST ${EXPORTS_URL}`]: jsonResponse(job("queued"), 202),
      [`GET ${JOB_URL}`]: jsonResponse(job("failed", error)),
    });
    vi.useFakeTimers();

    fireEvent.click(button("Generar borrador"));
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS));

    expect(screen.getByText("La exportación falló.")).toBeTruthy();
    expect(screen.getByText(`${error.cause} ${error.action}`)).toBeTruthy();
  });

  it("shows the server's V-11 and 403 errors in Spanish", async () => {
    let answer = jsonResponse({ title: "Conflict", status: 409, detail: V11_MESSAGE, code: "V-11" }, 409);
    await renderExport(
      planOut({ status: "VALIDATED" }),
      { [`POST ${EXPORTS_URL}`]: () => answer.clone() },
      "technician",
    );

    fireEvent.click(button("Generar exportación final"));
    expect((await screen.findByRole("alert")).textContent).toBe(V11_MESSAGE);

    answer = jsonResponse({ title: "Forbidden", status: 403 }, 403);
    fireEvent.click(button("Generar exportación final"));
    expect(await screen.findByText("No tienes permiso para esta acción en este expediente.")).toBeTruthy();
  });
});
