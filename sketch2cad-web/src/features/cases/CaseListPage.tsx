import { useEffect, useRef, useState, type CSSProperties } from "react";
import { api } from "../../api/client";
import {
  INCIDENT_TYPES,
  INCIDENT_TYPE_LABELS,
  NewCaseForm,
  type CaseFile,
  type IncidentType,
} from "./NewCaseForm";

/** Delay between the last keystroke in the search box and the request. */
export const SEARCH_DEBOUNCE_MS = 300;

const STATUS_LABELS: Record<string, string> = {
  open: "Abierto",
};

const STATUS_COLORS: Record<string, { background: string; color: string }> = {
  open: { background: "#FDEBC8", color: "#7A3E00" },
};

const DEFAULT_STATUS_COLOR = { background: "#E8EDF1", color: "#14202B" };

const styles = {
  page: {
    padding: 32,
    display: "flex",
    flexDirection: "column",
    gap: 24,
    background: "#F4F6F8",
    color: "#14202B",
    minHeight: "100%",
  },
  titleRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  heading: { margin: 0, fontSize: 28 },
  newButton: {
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
  filters: { display: "flex", gap: 12 },
  filterLabel: { fontSize: 13, display: "flex", flexDirection: "column", gap: 4 },
  search: {
    height: 40,
    width: 280,
    border: "1px solid #9AA8B5",
    borderRadius: 6,
    padding: "0 12px",
    fontSize: 14,
  },
  select: {
    height: 40,
    width: 200,
    border: "1px solid #9AA8B5",
    borderRadius: 6,
    padding: "0 8px",
    fontSize: 14,
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    background: "#fff",
    border: "1px solid #D5DCE3",
    fontSize: 14,
  },
  caption: { textAlign: "left", fontWeight: 600, padding: "0 0 8px" },
  headRow: { textAlign: "left", background: "#E8EDF1" },
  th: { padding: "12px 16px" },
  row: { borderTop: "1px solid #D5DCE3" },
  codeCell: { padding: "14px 16px", fontWeight: 600, textAlign: "left" },
  td: { padding: "14px 16px" },
  pill: { padding: "3px 10px", borderRadius: 12, fontSize: 13 },
  message: { margin: 0 },
  alert: { margin: 0, padding: "8px 10px", color: "#8A1C1C", background: "#FDECEC", borderRadius: 4 },
} satisfies Record<string, CSSProperties>;

type Result =
  | { key: string; kind: "ready"; items: CaseFile[] }
  | { key: string; kind: "error" };

function StatusPill({ status }: { status: string }) {
  const colors = STATUS_COLORS[status] ?? DEFAULT_STATUS_COLOR;
  return <span style={{ ...styles.pill, ...colors }}>{STATUS_LABELS[status] ?? status}</span>;
}

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function CaseListPage() {
  const [search, setSearch] = useState("");
  const [incidentType, setIncidentType] = useState<IncidentType | "">("");
  const [reloadCount, setReloadCount] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [createdCode, setCreatedCode] = useState<string | null>(null);
  const newButtonRef = useRef<HTMLButtonElement>(null);
  const formWasOpen = useRef(false);

  const query = useDebounced(search.trim(), SEARCH_DEBOUNCE_MS);
  const requestKey = JSON.stringify([query, incidentType, reloadCount]);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get("/api/v1/case-files", {
        query: {
          q: query === "" ? undefined : query,
          incident_type: incidentType === "" ? undefined : incidentType,
        },
        signal: controller.signal,
      })
      .then(
        ({ items }) => setResult({ key: requestKey, kind: "ready", items }),
        () => {
          if (!controller.signal.aborted) {
            setResult({ key: requestKey, kind: "error" });
          }
        },
      );
    return () => controller.abort();
  }, [requestKey, query, incidentType]);

  const current = result?.key === requestKey ? result : null;
  const filtered = query !== "" || incidentType !== "";

  useEffect(() => {
    // Return focus to the opener once the form closes; the button is disabled while open.
    if (!formOpen && formWasOpen.current) {
      newButtonRef.current?.focus();
    }
    formWasOpen.current = formOpen;
  }, [formOpen]);

  function closeForm(): void {
    setFormOpen(false);
  }

  function handleCreated(caseFile: CaseFile): void {
    setResult((previous) =>
      previous?.kind === "ready"
        ? { ...previous, items: [caseFile, ...previous.items.filter((c) => c.id !== caseFile.id)] }
        : { key: requestKey, kind: "ready", items: [caseFile] },
    );
    setCreatedCode(caseFile.code);
    closeForm();
  }

  return (
    <div style={styles.page}>
      <div style={styles.titleRow}>
        <h1 style={styles.heading}>Expedientes</h1>
        <button
          ref={newButtonRef}
          type="button"
          style={styles.newButton}
          disabled={formOpen}
          onClick={() => {
            setCreatedCode(null);
            setFormOpen(true);
          }}
        >
          Nuevo expediente
        </button>
      </div>
      {formOpen && <NewCaseForm onCreated={handleCreated} onCancel={closeForm} />}
      <p role="status" aria-live="polite" style={styles.message}>
        {createdCode !== null ? `Expediente ${createdCode} creado.` : ""}
      </p>
      <div role="search" style={styles.filters}>
        <label style={styles.filterLabel}>
          Buscar
          <input
            type="search"
            value={search}
            placeholder="Código o ubicación"
            onChange={(event) => setSearch(event.target.value)}
            style={styles.search}
          />
        </label>
        <label style={styles.filterLabel}>
          Tipo de hecho
          <select
            value={incidentType}
            onChange={(event) => setIncidentType(event.target.value as IncidentType | "")}
            style={styles.select}
          >
            <option value="">Todos</option>
            {INCIDENT_TYPES.map((type) => (
              <option key={type} value={type}>
                {INCIDENT_TYPE_LABELS[type]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {current === null && (
        <p role="status" aria-live="polite" style={styles.message}>
          Cargando expedientes…
        </p>
      )}
      {current?.kind === "error" && (
        <div role="alert" style={styles.alert}>
          No se pudieron cargar los expedientes.{" "}
          <button type="button" onClick={() => setReloadCount((count) => count + 1)}>
            Reintentar
          </button>
        </div>
      )}
      {current?.kind === "ready" && current.items.length === 0 && (
        <p style={styles.message}>
          {filtered ? "Ningún expediente coincide con la búsqueda." : "Aún no tienes expedientes."}
        </p>
      )}
      {current?.kind === "ready" && current.items.length > 0 && (
        <table style={styles.table}>
          <caption style={styles.caption}>Lista de expedientes</caption>
          <thead>
            <tr style={styles.headRow}>
              <th scope="col" style={styles.th}>
                Código
              </th>
              <th scope="col" style={styles.th}>
                Tipo de hecho
              </th>
              <th scope="col" style={styles.th}>
                Fecha
              </th>
              <th scope="col" style={styles.th}>
                Ubicación declarada
              </th>
              <th scope="col" style={styles.th}>
                Estado
              </th>
            </tr>
          </thead>
          <tbody>
            {current.items.map((caseFile) => (
              <tr key={caseFile.id} style={styles.row}>
                <th scope="row" style={styles.codeCell}>
                  {caseFile.code}
                </th>
                <td style={styles.td}>{INCIDENT_TYPE_LABELS[caseFile.incident_type]}</td>
                <td style={styles.td}>{caseFile.incident_date}</td>
                <td style={styles.td}>{caseFile.declared_location}</td>
                <td style={styles.td}>
                  <StatusPill status={caseFile.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
