// Table of a plan's exported files (prototype screen 6): Spanish kind, file name, a draft/final
// badge in words, size, the full SHA-256 (selectable, with a copy button), creation date and a
// "Descargar" button that opens the DownloadDialog. Focus returns to that button on close.
import { useRef, useState, type CSSProperties } from "react";
import { type ApiClient } from "../../api/client";
import { DownloadDialog } from "./DownloadDialog";
import { ARTIFACT_KIND_LABELS, EXPORT_MESSAGES as M, type ArtifactOut } from "./messages";

const sizeFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });
const timeFormat = new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "short" });

/** Size in KB below 1 MB, else in MB. */
export function formatSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${sizeFormat.format(bytes / 1024)} KB`
    : `${sizeFormat.format(bytes / (1024 * 1024))} MB`;
}

const styles = {
  table: { width: "100%", borderCollapse: "collapse", background: "#fff", border: "1px solid #D5DCE3", fontSize: 14 },
  cell: { padding: "8px 12px", borderTop: "1px solid #D5DCE3", textAlign: "left", verticalAlign: "top" },
  hash: { fontFamily: "ui-monospace,Menlo,monospace", fontSize: 12, userSelect: "all", wordBreak: "break-all" },
  badge: { border: "1px solid #7C8B99", borderRadius: 4, padding: "0 6px", fontWeight: 600 },
} satisfies Record<string, CSSProperties>;

function HashCell({ artifact }: { artifact: ArtifactOut }) {
  const [copied, setCopied] = useState(false);
  return (
    <td style={styles.cell}>
      <code style={styles.hash}>{artifact.sha256}</code>{" "}
      <button
        type="button"
        aria-label={`${M.copy} SHA-256 de ${artifact.filename}`}
        onClick={() => {
          void navigator.clipboard?.writeText(artifact.sha256).then(() => setCopied(true), () => undefined);
        }}
      >
        {copied ? M.copied : M.copy}
      </button>
    </td>
  );
}

export interface ArtifactTableProps {
  artifacts: ArtifactOut[];
  api: Pick<ApiClient, "get">;
}

export function ArtifactTable({ artifacts, api }: ArtifactTableProps) {
  const [selected, setSelected] = useState<ArtifactOut | null>(null);
  const opener = useRef<HTMLButtonElement | null>(null);

  if (artifacts.length === 0) return <p>{M.noArtifacts}</p>;
  const headers = [M.kind, M.filename, M.type, M.size, "SHA-256", M.created, M.actions];

  return (
    <>
      <table style={styles.table}>
        <caption style={{ textAlign: "left", fontWeight: 600, padding: "8px 0" }}>{M.artifacts}</caption>
        <thead>
          <tr style={{ background: "#E8EDF1" }}>
            {headers.map((header) => (
              <th key={header} scope="col" style={styles.cell}>
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {artifacts.map((artifact) => (
            <tr key={artifact.id}>
              <td style={styles.cell}>{ARTIFACT_KIND_LABELS[artifact.kind]}</td>
              <td style={styles.cell}>{artifact.filename}</td>
              <td style={styles.cell}>
                <span style={styles.badge}>{artifact.is_draft ? M.draftBadge : M.finalBadge}</span>
              </td>
              <td style={styles.cell}>{formatSize(artifact.byte_size)}</td>
              <HashCell artifact={artifact} />
              <td style={styles.cell}>{timeFormat.format(new Date(artifact.created_at))}</td>
              <td style={styles.cell}>
                <button
                  type="button"
                  aria-haspopup="dialog"
                  aria-label={`${M.download} ${artifact.filename}`}
                  onClick={(event) => {
                    opener.current = event.currentTarget;
                    setSelected(artifact);
                  }}
                >
                  {M.download}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected !== null && (
        <DownloadDialog
          artifact={selected}
          api={api}
          onClose={() => {
            setSelected(null);
            opener.current?.focus();
          }}
        />
      )}
    </>
  );
}
