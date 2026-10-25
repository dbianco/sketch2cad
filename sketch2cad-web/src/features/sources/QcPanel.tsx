import type { CSSProperties } from "react";

/** Semaphore status of the whole QC result and of each check. */
export type QcStatus = "green" | "amber" | "red";

/** One entry of `source.qc.checks`; `message` is already in Spanish. */
export interface QcCheck {
  name: string;
  status: QcStatus;
  value: number | null;
  threshold: number | null;
  message: string;
}

/** The `source.qc` JSON written by the QC job. */
export interface QcResult {
  status: QcStatus;
  checks: QcCheck[];
}

const QC_STATUSES: readonly string[] = ["green", "amber", "red"];

function isStatus(value: unknown): value is QcStatus {
  return typeof value === "string" && QC_STATUSES.includes(value);
}

/** Narrow the loosely typed `qc` of the API to a `QcResult`; `null` when it is not one. */
export function parseQc(raw: unknown): QcResult | null {
  if (raw === null || typeof raw !== "object") {
    return null;
  }
  const { status, checks } = raw as { status?: unknown; checks?: unknown };
  if (!isStatus(status) || !Array.isArray(checks)) {
    return null;
  }
  const parsed: QcCheck[] = [];
  for (const item of checks as unknown[]) {
    const check = (item ?? {}) as Partial<Record<keyof QcCheck, unknown>>;
    if (!isStatus(check.status) || typeof check.name !== "string") {
      continue;
    }
    parsed.push({
      name: check.name,
      status: check.status,
      value: typeof check.value === "number" ? check.value : null,
      threshold: typeof check.threshold === "number" ? check.threshold : null,
      message: typeof check.message === "string" ? check.message : "",
    });
  }
  return { status, checks: parsed };
}

export const CHECK_LABELS: Record<string, string> = {
  input: "Lectura de la imagen",
  short_side: "Resolución (lado corto)",
  laplacian_variance: "Nitidez (varianza del Laplaciano)",
  contrast: "Contraste",
  paper_crop: "Bordes del papel",
};

export const STATUS_LABELS: Record<QcStatus, string> = {
  green: "Verde",
  amber: "Ámbar",
  red: "Rojo",
};

/** What the technician can do for each overall status. */
export const STATUS_GUIDANCE: Record<QcStatus, string> = {
  green: "La fuente cumple los controles de calidad.",
  amber: "Las advertencias no te impiden continuar.",
  red: "La calidad es insuficiente para las sugerencias automáticas. Carga una foto o un escaneo mejor, o continúa en modo manual.",
};

const STATUS_COLORS: Record<QcStatus, string> = {
  green: "#145A32",
  amber: "#7A3E00",
  red: "#8A1C1C",
};

const NOTICE_COLORS: Record<QcStatus, { background: string; border: string }> = {
  green: { background: "#E6F4EA", border: "#7FBF8E" },
  amber: { background: "#FFF4DC", border: "#E5B85C" },
  red: { background: "#FDECEC", border: "#D98C8C" },
};

const styles = {
  list: {
    listStyle: "none",
    margin: 0,
    background: "#fff",
    border: "1px solid #D5DCE3",
    borderRadius: 8,
    padding: "8px 16px",
  },
  row: {
    display: "flex",
    justifyContent: "space-between",
    gap: 16,
    padding: "12px 0",
    fontSize: 14,
  },
  status: { fontWeight: 600 },
  notice: {
    borderRadius: 8,
    padding: "14px 16px",
    fontSize: 14,
    lineHeight: 1.45,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  reasons: { margin: 0, paddingLeft: 20 },
  summary: { margin: 0, fontWeight: 600 },
} satisfies Record<string, CSSProperties>;

const numberFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });

function formatValue(check: QcCheck): string {
  if (check.value === null) {
    return "";
  }
  const formatted = numberFormat.format(check.value);
  return check.name === "short_side" ? ` · ${formatted} px` : ` · ${formatted}`;
}

/** Traffic light of a source: one row per check, then the reasons and what to do. */
export function QcPanel({ qc }: { qc: QcResult }) {
  const reasons = qc.checks.filter((check) => check.status !== "green" && check.message !== "");
  return (
    <section aria-labelledby="qc-heading" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <p id="qc-heading" style={{ ...styles.summary, color: STATUS_COLORS[qc.status] }}>
        Resultado: {STATUS_LABELS[qc.status]}
      </p>
      <ul aria-label="Controles de calidad" style={styles.list}>
        {qc.checks.map((check, index) => (
          <li
            key={check.name}
            style={{ ...styles.row, borderTop: index === 0 ? undefined : "1px solid #E1E6EB" }}
          >
            <span>{CHECK_LABELS[check.name] ?? check.name}</span>
            <span style={{ ...styles.status, color: STATUS_COLORS[check.status] }}>
              ● {STATUS_LABELS[check.status]}
              {formatValue(check)}
            </span>
          </li>
        ))}
      </ul>
      <div
        role={qc.status === "red" ? "alert" : "status"}
        style={{
          ...styles.notice,
          background: NOTICE_COLORS[qc.status].background,
          border: `1px solid ${NOTICE_COLORS[qc.status].border}`,
        }}
      >
        {reasons.length > 0 && (
          <ul aria-label="Motivos" style={styles.reasons}>
            {reasons.map((check) => (
              <li key={check.name}>{check.message}</li>
            ))}
          </ul>
        )}
        <span>{STATUS_GUIDANCE[qc.status]}</span>
      </div>
    </section>
  );
}
