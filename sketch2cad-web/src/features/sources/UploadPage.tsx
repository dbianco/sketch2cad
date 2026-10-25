import { useEffect, useRef, useState, type CSSProperties, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ApiError, api } from "../../api/client";
import type { components } from "../../api/schema";
import { hashFile } from "./hashFile";
import { QcPanel, parseQc, type QcResult } from "./QcPanel";
import { SourceViewer } from "./SourceViewer";

export type Source = components["schemas"]["SourceOut"];
export type Job = components["schemas"]["JobOut"];

/** Delay between two `GET /jobs/{id}` requests while the QC job is not finished. */
export const JOB_POLL_INTERVAL_MS = 2000;

/** Upload size cap (V-02); the server enforces the same limit. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

/** Messages of the PRD validation rules, shown verbatim. */
export const UPLOAD_MESSAGES = {
  invalidType: "El archivo no es un JPEG, PNG, TIFF o PDF de una página válido.", // V-01
  tooLarge: "El archivo supera los 50 MB permitidos.", // V-02
  altered: "El archivo se alteró durante la subida. Vuelve a cargarlo.", // V-03
  generic: "No se pudo subir el archivo. Inténtalo de nuevo.",
  pollFailed: "No se pudo consultar el control de calidad. Recarga la página para reintentar.",
  qcUnavailable: "El control de calidad terminó, pero no se pudo obtener su resultado.",
} as const;

const ACCEPTED_TYPES = ".jpg,.jpeg,.png,.tif,.tiff,.pdf,image/jpeg,image/png,image/tiff,application/pdf";

const TERMINAL_JOB_STATUSES: ReadonlySet<Job["status"]> = new Set(["succeeded", "failed"]);

/** URL of the upload screen of a case file (route `/cases/:caseId/upload`). */
export function uploadPath(caseId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/upload`;
}

/** URL of the stored original bytes of a source (explicit download only, never the viewer). */
export function originalUrl(sourceId: string): string {
  return `/api/v1/sources/${encodeURIComponent(sourceId)}/original`;
}

/** Path of the calibration screen of a source (built in H2). */
export function calibrationPath(caseId: string, sourceId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/sources/${encodeURIComponent(sourceId)}/calibration`;
}

type Phase =
  | { kind: "idle" }
  | { kind: "hashing"; fileName: string; progress: number }
  | { kind: "uploading"; fileName: string }
  | { kind: "error"; message: string }
  | { kind: "uploaded"; source: Source; clientSha256: string };

type JobState =
  | { kind: "pending"; status: Job["status"] }
  | { kind: "done"; job: Job; source: Source | null }
  | { kind: "unreachable" };

const styles = {
  page: {
    padding: 32,
    display: "flex",
    gap: 32,
    flexWrap: "wrap",
    background: "#F4F6F8",
    color: "#14202B",
    minHeight: "100%",
  },
  column: { flex: "1 1 360px", display: "flex", flexDirection: "column", gap: 20 },
  heading: { margin: 0, fontSize: 26 },
  subheading: { margin: 0, fontSize: 22 },
  dropZone: {
    minHeight: 180,
    border: "2px dashed #7C8B99",
    borderRadius: 8,
    background: "#fff",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  },
  dropTitle: { fontSize: 16, fontWeight: 600 },
  hint: { fontSize: 13, color: "#4A5966" },
  card: {
    background: "#fff",
    border: "1px solid #D5DCE3",
    borderRadius: 8,
    padding: 16,
    fontSize: 14,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  hash: { fontFamily: "ui-monospace,Menlo,monospace", fontSize: 12, wordBreak: "break-all" },
  verified: { color: "#145A32", fontWeight: 600 },
  alert: { margin: 0, padding: "8px 10px", color: "#8A1C1C", background: "#FDECEC", borderRadius: 4 },
  message: { margin: 0 },
  actions: { display: "flex", gap: 12, marginTop: "auto" },
  primary: {
    height: 44,
    padding: "0 20px",
    background: "#0B6E8A",
    color: "#fff",
    border: 0,
    borderRadius: 6,
    fontSize: 15,
    fontWeight: 600,
    cursor: "pointer",
  },
  secondary: {
    height: 44,
    padding: "0 20px",
    background: "#fff",
    color: "#14202B",
    border: "1px solid #7C8B99",
    borderRadius: 6,
    fontSize: 15,
    cursor: "pointer",
  },
} satisfies Record<string, CSSProperties>;

const numberFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });

function formatSize(bytes: number): string {
  return `${numberFormat.format(bytes / (1024 * 1024))} MB`;
}

function shortHash(hash: string): string {
  return `${hash.slice(0, 4)}…${hash.slice(-4)}`;
}

function uploadErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413) return UPLOAD_MESSAGES.tooLarge;
    if (error.status === 415) return UPLOAD_MESSAGES.invalidType;
    if (error.status === 422) return UPLOAD_MESSAGES.altered;
  }
  return UPLOAD_MESSAGES.generic;
}

/** `GET /sources/{id}` after the QC job succeeded; `null` when it cannot be fetched. */
async function fetchFinishedSource(sourceId: string, signal: AbortSignal): Promise<Source | null> {
  try {
    return await api.get("/api/v1/sources/{source_id}", { path: { source_id: sourceId }, signal });
  } catch {
    return null;
  }
}

/**
 * Poll the QC job every `JOB_POLL_INTERVAL_MS` until it succeeds or fails; on success,
 * fetch the source once to get its finished `qc`.
 */
function useJobStatus(jobId: string | null, sourceId: string | null): JobState | null {
  const [state, setState] = useState<{ jobId: string; value: JobState } | null>(null);

  useEffect(() => {
    if (jobId === null || sourceId === null) {
      return;
    }
    const finishedSourceId = sourceId;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll(id: string): Promise<void> {
      try {
        const job = await api.get("/api/v1/jobs/{job_id}", {
          path: { job_id: id },
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (TERMINAL_JOB_STATUSES.has(job.status)) {
          const finished =
            job.status === "succeeded" ? await fetchFinishedSource(finishedSourceId, controller.signal) : null;
          if (controller.signal.aborted) return;
          setState({ jobId: id, value: { kind: "done", job, source: finished } });
          return;
        }
        setState({ jobId: id, value: { kind: "pending", status: job.status } });
        timer = setTimeout(() => void poll(id), JOB_POLL_INTERVAL_MS);
      } catch {
        if (!controller.signal.aborted) {
          setState({ jobId: id, value: { kind: "unreachable" } });
        }
      }
    }

    timer = setTimeout(() => void poll(jobId), JOB_POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId, sourceId]);

  if (jobId === null || sourceId === null) return null;
  return state?.jobId === jobId ? state.value : { kind: "pending", status: "queued" };
}

function QcSection({ qc, job }: { qc: QcResult | null; job: JobState }) {
  if (qc !== null) {
    return <QcPanel qc={qc} />;
  }
  if (job.kind === "pending") {
    return (
      <p role="status" aria-live="polite" style={styles.message}>
        Analizando la calidad de la imagen…
      </p>
    );
  }
  if (job.kind === "unreachable") {
    return (
      <p role="alert" style={styles.alert}>
        {UPLOAD_MESSAGES.pollFailed}
      </p>
    );
  }
  if (job.job.status === "failed") {
    return (
      <div role="alert" style={styles.alert}>
        <p style={styles.message}>{job.job.error?.cause ?? "El control de calidad falló."}</p>
        <p style={styles.message}>{job.job.error?.action ?? "Vuelve a cargar el archivo."}</p>
      </div>
    );
  }
  return (
    <p role="status" style={styles.message}>
      {UPLOAD_MESSAGES.qcUnavailable}
    </p>
  );
}

export function UploadPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const source = phase.kind === "uploaded" ? phase.source : null;
  const job = useJobStatus(source?.qc_job_id ?? null, source?.id ?? null);
  // `qc` is null in the 201 until the job runs; the finished source carries it afterwards.
  const finishedQc = job?.kind === "done" && job.source !== null ? job.source.qc : null;
  const qc = parseQc(finishedQc ?? source?.qc ?? null);
  const busy = phase.kind === "hashing" || phase.kind === "uploading";

  async function upload(file: File): Promise<void> {
    if (file.size > MAX_UPLOAD_BYTES) {
      setPhase({ kind: "error", message: UPLOAD_MESSAGES.tooLarge });
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setPhase({ kind: "hashing", fileName: file.name, progress: 0 });
      const clientSha256 = await hashFile(file, {
        signal: controller.signal,
        onProgress: (progress) => {
          if (!controller.signal.aborted) {
            setPhase({ kind: "hashing", fileName: file.name, progress });
          }
        },
      });
      setPhase({ kind: "uploading", fileName: file.name });
      const form = new FormData();
      form.append("client_sha256", clientSha256);
      form.append("file", file, file.name);
      const created = await api.post("/api/v1/case-files/{case_id}/sources", {
        path: { case_id: caseId },
        body: form,
        signal: controller.signal,
      });
      setPhase({ kind: "uploaded", source: created, clientSha256 });
    } catch (error) {
      if (!controller.signal.aborted) {
        setPhase({ kind: "error", message: uploadErrorMessage(error) });
      }
    }
  }

  function handleFiles(files: FileList | null): void {
    const file = files?.[0];
    if (file !== undefined && !busy) {
      void upload(file);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    handleFiles(event.dataTransfer.files);
  }

  function reset(): void {
    abortRef.current?.abort();
    setPhase({ kind: "idle" });
    if (inputRef.current !== null) {
      inputRef.current.value = "";
    }
  }

  const canContinue =
    source !== null && job !== null && (qc !== null || (job.kind === "done" && job.job.status === "succeeded"));

  return (
    <div style={styles.page}>
      <div style={styles.column}>
        <Link to="/">Volver a expedientes</Link>
        <h1 style={styles.heading}>Cargar el croquis</h1>
        {source === null && (
          <div style={styles.dropZone} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
            <div style={styles.dropTitle}>Arrastra un archivo o elígelo</div>
            <div style={styles.hint}>JPEG, PNG, TIFF o PDF de una página · máximo 50 MB</div>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPTED_TYPES}
              aria-label="Archivo del croquis"
              hidden
              onChange={(event) => handleFiles(event.target.files)}
            />
            <button
              type="button"
              style={styles.primary}
              disabled={busy}
              onClick={() => inputRef.current?.click()}
            >
              Elegir archivo
            </button>
          </div>
        )}
        {phase.kind === "hashing" && (
          <p role="status" aria-live="polite" style={styles.message}>
            Calculando la huella SHA-256 de {phase.fileName}… {Math.round(phase.progress * 100)} %
          </p>
        )}
        {phase.kind === "uploading" && (
          <p role="status" aria-live="polite" style={styles.message}>
            Subiendo {phase.fileName}…
          </p>
        )}
        {phase.kind === "error" && (
          <p role="alert" style={styles.alert}>
            {phase.message}
          </p>
        )}
        {phase.kind === "uploaded" && (
          <div style={styles.card}>
            <div style={{ fontWeight: 600 }}>{phase.source.original_filename}</div>
            <div style={styles.hint}>
              {phase.source.width_px !== null && phase.source.height_px !== null
                ? `${String(phase.source.width_px)} × ${String(phase.source.height_px)} px · `
                : ""}
              {formatSize(phase.source.byte_size)}
            </div>
            <div style={styles.hash}>
              SHA-256 {shortHash(phase.source.sha256)} (servidor) = {shortHash(phase.clientSha256)}{" "}
              (cliente)
            </div>
            {phase.source.sha256 === phase.clientSha256 && (
              <div style={styles.verified}>Hash verificado. Original guardado sin cambios.</div>
            )}
            <div style={styles.hash}>
              SHA-256 del original: <span data-testid="source-sha256">{phase.source.sha256}</span>
            </div>
            <a href={originalUrl(phase.source.id)} download={phase.source.original_filename}>
              Descargar el original
            </a>
          </div>
        )}
        {phase.kind === "uploaded" && (
          <SourceViewer sourceId={phase.source.id} alt={phase.source.original_filename} />
        )}
      </div>
      {source !== null && job !== null && (
        <div style={styles.column}>
          <h2 style={styles.subheading}>Control de calidad</h2>
          <QcSection qc={qc} job={job} />
          <div style={styles.actions}>
            <button type="button" style={styles.secondary} onClick={reset}>
              Cargar otra fuente
            </button>
            <button
              type="button"
              style={styles.primary}
              disabled={!canContinue}
              onClick={() => void navigate(calibrationPath(caseId, source.id))}
            >
              Continuar a calibración
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
