import { ApiError } from "../../api/client";
import type { components } from "../../api/schema";
import { problemCode, type PlanStatus } from "../plans/messages";

export type ArtifactOut = components["schemas"]["ArtifactOut"];
export type ArtifactKind = ArtifactOut["kind"];
export type JobStatus = components["schemas"]["JobOut"]["status"];

/** Route of the export page of a plan (here so the plan pages link to it without an import cycle). */
export function planExportPath(caseId: string, planId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/plans/${encodeURIComponent(planId)}/export`;
}

/** V-11: a final export needs a validated plan (same text as the API). */
export const V11_MESSAGE = "La exportación final requiere un plano validado.";
/** `draft_not_allowed`: a draft needs a calibrated plan, in EDITING or later (same text as the API). */
export const DRAFT_NOT_ALLOWED_MESSAGE =
  "La exportación borrador requiere un plano calibrado, en edición o posterior.";
/** Legal notice of the final documents (task 001-089, `legal.disclaimer_enabled`). */
export const DISCLAIMER_TEXT =
  "Borrador técnico, no apto como prueba hasta contar con revisión jurídica.";
export const SIGNATURE_NOTICE = "La firma prueba integridad y origen técnico, no autoría pericial.";

/** Plan statuses that accept a draft export (the API's DRAFT_STATUSES). */
export const DRAFT_STATUSES: ReadonlySet<PlanStatus> = new Set([
  "EDITING",
  "REVIEW_PENDING",
  "IN_REVIEW",
  "VALIDATED",
  "EXPORTED",
]);

export const EXPORT_MESSAGES = {
  title: "Exportación",
  loading: "Cargando…",
  loadFailed: "No se pudo cargar el plano. Recarga la página para reintentar.",
  backToReview: "Ir a la revisión",
  openEditor: "Abrir el editor",
  options: "Opciones de la hoja",
  paper: "Papel",
  scale: "Escala",
  scaleHint: "Con «Automática» el servidor propone la escala más grande con la que el plano cabe en la hoja.",
  draftTitle: "Borrador",
  draftButton: "Generar borrador",
  draftNote: "El borrador lleva la marca de agua BORRADOR / DRAFT y no incluye manifiesto firmado.",
  finalTitle: "Final",
  finalButton: "Generar exportación final",
  finalNote: "Genera el paquete firmado (DXF, PDF, reporte de auditoría y manifiesto) de la revisión validada.",
  artifacts: "Archivos generados",
  noArtifacts: "Todavía no hay archivos exportados.",
  artifactsFailed: "No se pudo cargar la lista de archivos.",
  exportFailed: "No se pudo iniciar la exportación. Inténtalo de nuevo.",
  forbidden: "No tienes permiso para esta acción en este expediente.",
  jobUnreachable: "No se pudo consultar el estado de la exportación. Recarga la página.",
  kind: "Tipo",
  filename: "Archivo",
  type: "Borrador o final",
  size: "Tamaño",
  created: "Creado",
  actions: "Acciones",
  draftBadge: "Borrador",
  finalBadge: "Final",
  copy: "Copiar",
  copied: "Copiado",
  download: "Descargar",
  dialogTitle: "Confirmar la descarga",
  dialogIntro: "Revisa los hashes antes de confirmar. La descarga queda registrada en la auditoría.",
  fileToDownload: "Archivo a descargar",
  packageFiles: "Archivos del paquete firmado",
  snapshot: "Instantánea de la revisión",
  signature: "Firma",
  signatureScheme: "Esquema",
  signatureKey: "Clave pública",
  eventsChain: "Cadena de eventos",
  eventsCount: "Eventos",
  eventsLastHash: "Último hash",
  draftNoManifest: "Los borradores no llevan manifiesto firmado: solo se muestra el hash del archivo.",
  detailsLoading: "Cargando el manifiesto…",
  detailsFailed: "No se pudo cargar el manifiesto del archivo.",
  confirm: "Confirmar y descargar",
  cancel: "Cancelar",
  close: "Cerrar",
  downloading: "Descargando…",
  hashVerified: "Descarga completada. Hash verificado.",
  hashMismatch: "El hash del archivo descargado no coincide con el registrado. No uses este archivo.",
  downloadFailed: "No se pudo descargar el archivo. Inténtalo de nuevo.",
} as const;

export const ARTIFACT_KIND_LABELS: Readonly<Record<ArtifactKind, string>> = {
  dxf: "Plano DXF",
  pdf_plan: "Plano PDF",
  pdf_audit: "Reporte de auditoría PDF",
  manifest: "Manifiesto firmado",
  zip: "Paquete ZIP",
  rectified_image: "Imagen rectificada",
};

export const PAPER_OPTIONS = [
  { value: "auto", label: "Automática" },
  { value: "A4", label: "A4" },
  { value: "A3", label: "A3" },
] as const;

export const SCALE_OPTIONS = [
  { value: "auto", label: "Automática — la más grande que cabe" },
  { value: "50", label: "1:50" },
  { value: "100", label: "1:100" },
  { value: "200", label: "1:200" },
  { value: "250", label: "1:250" },
  { value: "500", label: "1:500" },
] as const;

export const JOB_STATUS_LABELS: Readonly<Record<JobStatus, string>> = {
  queued: "Exportación en cola…",
  running: "Generando la exportación…",
  succeeded: "Exportación completada.",
  failed: "La exportación falló.",
};

/** Spanish message of an export or download error; `fallback` for anything unexpected. */
export function exportErrorMessage(error: unknown, fallback: string): string {
  const code = problemCode(error);
  if (code === "V-11") return V11_MESSAGE;
  if (code === "draft_not_allowed") return DRAFT_NOT_ALLOWED_MESSAGE;
  if (error instanceof ApiError && error.status === 403) return EXPORT_MESSAGES.forbidden;
  return fallback;
}
