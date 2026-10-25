import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hashFile, sha256Hex } from "./hashFile";
import type { QcResult } from "./QcPanel";
import {
  JOB_POLL_INTERVAL_MS,
  UPLOAD_MESSAGES,
  UploadPage,
  type Job,
  type Source,
} from "./UploadPage";

const CASE_ID = "00000000-0000-4000-8000-0000000000c1";
const SOURCE_ID = "00000000-0000-4000-8000-0000000000s1";
const JOB_ID = "00000000-0000-4000-8000-0000000000j1";
const UPLOAD_URL = `/api/v1/case-files/${CASE_ID}/sources`;
const JOB_URL = `/api/v1/jobs/${JOB_ID}`;

const FILE_BYTES = new TextEncoder().encode("croquis de prueba");
const FILE_SHA256 = sha256Hex(FILE_BYTES);

const AMBER_QC: QcResult = {
  status: "amber",
  checks: [
    {
      name: "short_side",
      status: "green",
      value: 3024,
      threshold: 1500,
      message: "Resolución adecuada: el lado corto mide 3024 px.",
    },
    {
      name: "laplacian_variance",
      status: "amber",
      value: 62,
      threshold: 100,
      message: "Imagen algo borrosa: algunos trazos o cotas pueden no leerse.",
    },
    { name: "contrast", status: "green", value: 80, threshold: 60, message: "Contraste adecuado." },
  ],
};

const RED_QC: QcResult = {
  status: "red",
  checks: [
    {
      name: "short_side",
      status: "red",
      value: 800,
      threshold: 900,
      message: "Resolución insuficiente: el lado corto mide 800 px (mínimo 900 px).",
    },
  ],
};

function source(overrides: Partial<Source> = {}): Source {
  return {
    id: SOURCE_ID,
    case_file_id: CASE_ID,
    original_filename: "croquis_cruce_01.jpg",
    media_type: "image/jpeg",
    byte_size: FILE_BYTES.length,
    sha256: FILE_SHA256,
    width_px: 4032,
    height_px: 3024,
    dpi: null,
    exif: {},
    qc: null,
    qc_job_id: JOB_ID,
    uploaded_by: "8a7c0d4e-0000-4000-8000-000000000001",
    uploaded_at: "2026-10-25T10:00:00Z",
    ...overrides,
  };
}

function job(status: Job["status"], error: Job["error"] = null): Job {
  return { id: JOB_ID, kind: "qc", status, source_id: SOURCE_ID, error, result_ref: null };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function problem(status: number, detail: string): Response {
  return jsonResponse({ type: "about:blank", title: "Error", status, detail }, status);
}

const fetchMock = vi.fn<typeof fetch>();

/** Serve the upload with `uploadResponse` and the job polls with `jobs`, in order. */
function mockApi(uploadResponse: () => Response, jobs: Job[] = []): void {
  const queue = [...jobs];
  fetchMock.mockImplementation((input, init) => {
    const url = String(input);
    if (init?.method === "POST" && url === UPLOAD_URL) {
      return Promise.resolve(uploadResponse());
    }
    if (url === JOB_URL) {
      const next = queue.length > 1 ? queue.shift() : queue[0];
      return Promise.resolve(next === undefined ? problem(404, "Not found") : jsonResponse(next));
    }
    return Promise.resolve(problem(404, "Not found"));
  });
}

function jobCalls(): number {
  return fetchMock.mock.calls.filter(([input]) => String(input) === JOB_URL).length;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/cases/${CASE_ID}/upload`]}>
      <Routes>
        <Route path="/cases/:caseId/upload" element={<UploadPage />} />
        <Route path="*" element={<p>Otra pantalla</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function chooseFile(bytes: Uint8Array<ArrayBuffer> = FILE_BYTES, name = "croquis_cruce_01.jpg"): File {
  const file = new File([bytes], name, { type: "image/jpeg" });
  fireEvent.change(screen.getByLabelText("Archivo del croquis"), { target: { files: [file] } });
  return file;
}

/** Let pending promises (hashing, fetch, body parsing) settle under fake timers. */
async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

function continueButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Continuar a calibración" });
}

describe("hashFile", () => {
  it("returns the known SHA-256 of a known byte array", () => {
    expect(sha256Hex(new TextEncoder().encode("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(sha256Hex(new Uint8Array())).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
  });

  it("hashes a file in chunks to the same digest as Web Crypto", async () => {
    const bytes = new Uint8Array(1000).map((_, index) => (index * 31 + 7) % 256);
    const progress: number[] = [];

    const digest = await hashFile(new Blob([bytes]), {
      chunkBytes: 37,
      onProgress: (fraction) => progress.push(fraction),
    });

    const expected = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    expect(digest).toBe(expected);
    expect(progress).toHaveLength(Math.ceil(1000 / 37));
    expect(progress.at(-1)).toBe(1);
  });
});

describe("UploadPage", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.cookie = "s2c_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("uploads the file with its client SHA-256 and the CSRF header", async () => {
    document.cookie = "s2c_csrf=csrf-token-123; path=/";
    mockApi(() => jsonResponse(source(), 201), [job("queued")]);
    renderPage();

    const file = chooseFile();
    await settle();

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(String(post?.[0])).toBe(UPLOAD_URL);
    expect(new Headers(post?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-token-123");
    const form = post?.[1]?.body as FormData;
    expect(form.get("client_sha256")).toBe(FILE_SHA256);
    expect((form.get("file") as File).name).toBe(file.name);
    expect(screen.getByText("Hash verificado. Original guardado sin cambios.")).toBeTruthy();
    expect(screen.getByText("Analizando la calidad de la imagen…")).toBeTruthy();
    expect(continueButton().disabled).toBe(true);
  });

  it.each([
    [413, "El archivo supera los 50 MB permitidos."],
    [415, "El archivo no es un JPEG, PNG, TIFF o PDF de una página válido."],
    [422, "El archivo se alteró durante la subida. Vuelve a cargarlo."],
  ])("renders the validation message of a %i response", async (status, message) => {
    mockApi(() => problem(status, "Rejected"));
    renderPage();

    chooseFile();
    await settle();

    expect(screen.getByRole("alert").textContent).toBe(message);
    expect(jobCalls()).toBe(0);
  });

  it("rejects a file over 50 MB before hashing or uploading it", async () => {
    mockApi(() => jsonResponse(source(), 201));
    renderPage();

    const file = new File([FILE_BYTES], "enorme.tif");
    Object.defineProperty(file, "size", { value: 50 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByLabelText("Archivo del croquis"), { target: { files: [file] } });
    await settle();

    expect(screen.getByRole("alert").textContent).toBe(UPLOAD_MESSAGES.tooLarge);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("polls the job every 2 s and stops once it succeeds", async () => {
    mockApi(() => jsonResponse(source(), 201), [job("queued"), job("running"), job("succeeded")]);
    renderPage();

    chooseFile();
    await settle();
    expect(jobCalls()).toBe(0);

    await settle(JOB_POLL_INTERVAL_MS - 1);
    expect(jobCalls()).toBe(0);
    await settle(1);
    expect(jobCalls()).toBe(1);
    await settle(JOB_POLL_INTERVAL_MS);
    expect(jobCalls()).toBe(2);
    await settle(JOB_POLL_INTERVAL_MS);
    expect(jobCalls()).toBe(3);

    await settle(JOB_POLL_INTERVAL_MS * 5);
    expect(jobCalls()).toBe(3);
    expect(vi.getTimerCount()).toBe(0);
    expect(continueButton().disabled).toBe(false);
  });

  it("stops polling when the job fails and shows its cause and action", async () => {
    const error = {
      cause: "No se pudo leer la imagen: el archivo está dañado o incompleto.",
      action: "Vuelve a cargar el archivo original o una nueva foto o escaneo del croquis.",
    };
    mockApi(() => jsonResponse(source(), 201), [job("running"), job("failed", error)]);
    renderPage();

    chooseFile();
    await settle();
    await settle(JOB_POLL_INTERVAL_MS);
    await settle(JOB_POLL_INTERVAL_MS);
    expect(jobCalls()).toBe(2);

    await settle(JOB_POLL_INTERVAL_MS * 5);
    expect(jobCalls()).toBe(2);
    expect(vi.getTimerCount()).toBe(0);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(error.cause);
    expect(alert.textContent).toContain(error.action);
    expect(continueButton().disabled).toBe(true);
  });

  it("shows the amber reasons and keeps the continue button enabled", async () => {
    mockApi(() => jsonResponse(source({ qc: { ...AMBER_QC } }), 201), [job("succeeded")]);
    renderPage();

    chooseFile();
    await settle();

    expect(screen.getByText("Resultado: Ámbar")).toBeTruthy();
    const reasons = screen.getByRole("list", { name: "Motivos" });
    expect(within(reasons).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Imagen algo borrosa: algunos trazos o cotas pueden no leerse.",
    ]);
    expect(screen.getByText("Las advertencias no te impiden continuar.")).toBeTruthy();
    const checks = screen.getByRole("list", { name: "Controles de calidad" });
    expect(within(checks).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Resolución (lado corto)● Verde · 3024 px",
      "Nitidez (varianza del Laplaciano)● Ámbar · 62",
      "Contraste● Verde · 80",
    ]);
    expect(continueButton().disabled).toBe(false);

    fireEvent.click(continueButton());
    expect(screen.getByText("Otra pantalla")).toBeTruthy();
  });

  it("shows the red reasons with the action to take", async () => {
    mockApi(() => jsonResponse(source({ qc: { ...RED_QC } }), 201), [job("succeeded")]);
    renderPage();

    chooseFile();
    await settle();

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(
      "Resolución insuficiente: el lado corto mide 800 px (mínimo 900 px).",
    );
    expect(alert.textContent).toContain("Carga una foto o un escaneo mejor, o continúa en modo manual.");
    fireEvent.click(screen.getByRole("button", { name: "Cargar otra fuente" }));
    expect(screen.getByRole("button", { name: "Elegir archivo" })).toBeTruthy();
  });
});
