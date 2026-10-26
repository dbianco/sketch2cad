// Download confirmation (US-16, spec section 13): before any byte is downloaded the dialog fetches
// `GET /artifacts/{id}` WITHOUT `confirm` (no audit event) and lists every hash: the file's own
// SHA-256 and, for a final artifact, each file of the signed manifest, the revision snapshot, the
// signature scheme and key and the event chain. Only "Confirmar y descargar" requests
// `?confirm=true` (one `artifact.downloaded` event); the blob is hashed in the browser and only
// saved when it matches the listed SHA-256. Focus is trapped in the dialog; Esc cancels.
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { type ApiClient } from "../../api/client";
import { hashFile } from "../sources/hashFile";
import {
  DISCLAIMER_TEXT,
  EXPORT_MESSAGES as M,
  SIGNATURE_NOTICE,
  exportErrorMessage,
  type ArtifactOut,
} from "./messages";

interface ManifestView {
  files: { kind: string; filename: string; sha256: string }[];
  snapshotSha256: string | null;
  signature: { scheme: string; publicKeyId: string } | null;
  events: { count: number; lastHash: string } | null;
}

type Details = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; manifest: ManifestView | null };
type Phase = "idle" | "downloading" | "verified" | "mismatch" | "failed";

const FOCUSABLE =
  "button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea, [tabindex]:not([tabindex='-1'])";

const styles = {
  backdrop: {
    position: "fixed",
    inset: 0,
    background: "rgba(20, 32, 43, 0.45)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  dialog: { background: "#fff", color: "#14202B", borderRadius: 8, padding: 24, maxWidth: 760, maxHeight: "85vh", overflowY: "auto" },
  hash: { fontFamily: "ui-monospace,Menlo,monospace", fontSize: 12, userSelect: "all", wordBreak: "break-all" },
  notice: { background: "#FFF4DC", border: "1px solid #E5B85C", borderRadius: 8, padding: "10px 14px", lineHeight: 1.5 },
} satisfies Record<string, CSSProperties>;

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
const text = (value: unknown): string => (typeof value === "string" ? value : "");

/** The parts of a spec 14.2 manifest the dialog lists; tolerant of missing members. */
function manifestView(manifest: Record<string, unknown>): ManifestView {
  const files = Array.isArray(manifest.artifacts) ? manifest.artifacts.map(record) : [];
  const signature = record(manifest.signature);
  const events = record(manifest.events_chain);
  return {
    files: files.map((file) => ({ kind: text(file.kind), filename: text(file.filename), sha256: text(file.sha256) })),
    snapshotSha256: text(record(manifest.revision).snapshot_sha256) || null,
    signature: "scheme" in signature ? { scheme: text(signature.scheme), publicKeyId: text(signature.public_key_id) } : null,
    events: typeof events.count === "number" ? { count: events.count, lastHash: text(events.last_hash) } : null,
  };
}

/** `GET /artifacts/{id}?confirm=true` (the one request that records `artifact.downloaded`). */
export function downloadUrl(artifactId: string): string {
  return `/api/v1/artifacts/${encodeURIComponent(artifactId)}?confirm=true`;
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function HashRow({ label, hash }: { label: string; hash: string }) {
  return (
    <li>
      {label}: <code style={styles.hash}>{hash}</code>
    </li>
  );
}

export interface DownloadDialogProps {
  artifact: ArtifactOut;
  onClose(): void;
  api: Pick<ApiClient, "get">;
}

export function DownloadDialog({ artifact, onClose, api }: DownloadDialogProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const controller = useRef(new AbortController());
  const [details, setDetails] = useState<Details>({ kind: "loading" });
  const [phase, setPhase] = useState<Phase>("idle");
  const [downloadError, setDownloadError] = useState<string>(M.downloadFailed);

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    api.get("/api/v1/artifacts/{artifact_id}", { path: { artifact_id: artifact.id }, signal: abort.signal }).then(
      (detail) => setDetails({ kind: "ready", manifest: detail.manifest === null ? null : manifestView(detail.manifest) }),
      (error: unknown) => {
        if (!abort.signal.aborted) setDetails({ kind: "error", message: exportErrorMessage(error, M.detailsFailed) });
      },
    );
    return () => abort.abort();
  }, [api, artifact.id]);

  useEffect(() => dialogRef.current?.focus(), []);

  async function download(): Promise<void> {
    setPhase("downloading");
    const { signal } = controller.current;
    try {
      const response = await fetch(downloadUrl(artifact.id), { credentials: "include", signal });
      if (!response.ok) {
        setDownloadError(response.status === 403 ? M.forbidden : M.downloadFailed);
        setPhase("failed");
        return;
      }
      const blob = await response.blob();
      const digest = await hashFile(blob, { signal });
      if (digest !== artifact.sha256) {
        setPhase("mismatch");
        return;
      }
      saveBlob(blob, artifact.filename);
      setPhase("verified");
    } catch {
      if (signal.aborted) return;
      setDownloadError(M.downloadFailed);
      setPhase("failed");
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (first === undefined || last === undefined) return;
    const inside = focusable.includes(document.activeElement as HTMLElement);
    if (event.shiftKey && (!inside || document.activeElement === first)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
      event.preventDefault();
      first.focus();
    }
  }

  const manifest = details.kind === "ready" ? details.manifest : null;
  const done = phase === "verified";
  const status = {
    idle: "",
    downloading: M.downloading,
    verified: M.hashVerified,
    mismatch: M.hashMismatch,
    failed: downloadError,
  }[phase];

  return (
    <div style={styles.backdrop}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={styles.dialog}
        onKeyDown={onKeyDown}
      >
        <h2 id={titleId} style={{ marginTop: 0, fontSize: 20 }}>
          {M.dialogTitle}
        </h2>
        <p>{M.dialogIntro}</p>
        <h3 style={{ fontSize: 16 }}>{M.fileToDownload}</h3>
        <ul aria-label={M.fileToDownload}>
          <HashRow label={artifact.filename} hash={artifact.sha256} />
        </ul>
        {details.kind === "loading" && <p role="status">{M.detailsLoading}</p>}
        {details.kind === "error" && <p role="alert">{details.message}</p>}
        {details.kind === "ready" && manifest === null && <p>{M.draftNoManifest}</p>}
        {manifest !== null && (
          <>
            <h3 style={{ fontSize: 16 }}>{M.packageFiles}</h3>
            <ul aria-label={M.packageFiles}>
              {manifest.files.map((file) => (
                <HashRow key={file.filename} label={file.filename} hash={file.sha256} />
              ))}
            </ul>
            <ul aria-label={M.signature}>
              {manifest.snapshotSha256 !== null && <HashRow label={M.snapshot} hash={manifest.snapshotSha256} />}
              {manifest.signature !== null && (
                <li>
                  {M.signature}: {M.signatureScheme} {manifest.signature.scheme} · {M.signatureKey}{" "}
                  <code style={styles.hash}>{manifest.signature.publicKeyId}</code>
                </li>
              )}
              {manifest.events !== null && (
                <li>
                  {M.eventsChain}: {M.eventsCount} {manifest.events.count} · {M.eventsLastHash}{" "}
                  <code style={styles.hash}>{manifest.events.lastHash}</code>
                </li>
              )}
            </ul>
          </>
        )}
        <p style={styles.notice}>
          {DISCLAIMER_TEXT} {SIGNATURE_NOTICE}
        </p>
        <p role="status" aria-live="polite">
          {status}
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          {!done && (
            <button
              type="button"
              disabled={details.kind !== "ready" || phase === "downloading"}
              onClick={() => void download()}
            >
              {M.confirm}
            </button>
          )}
          <button type="button" onClick={onClose}>
            {done ? M.close : M.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}
