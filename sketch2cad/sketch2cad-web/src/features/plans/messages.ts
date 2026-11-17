import { ApiError } from "../../api/client";
import type { components } from "../../api/schema";
import type { LeaseOut } from "../../editor/plan/planStore";
import { v12Message } from "../calibration/messages";

export type PlanStatus = components["schemas"]["PlanStatus"];
export type MetricStatus = components["schemas"]["MetricStatus"];

export { v12Message };

/** V-10: a validated plan is not edited without reopening it. */
export const V10_MESSAGE = "Este plano está validado. Reábrelo para editarlo.";
/** V-09: only a reviewer validates; a rejection needs its reason. */
export const V09_VALIDATE_MESSAGE = "Solo un revisor puede validar este plano.";
export const V09_REASON_MESSAGE = "Indica el motivo del rechazo.";

/** Spanish UI strings of the review page, the transition bar and the lease banner. */
export const PLAN_MESSAGES = {
  reviewTitle: "Revisión del plano",
  editorTitle: "Editor del plano",
  loading: "Cargando…",
  loadFailed: "No se pudo cargar el plano. Recarga la página para reintentar.",
  status: "Estado del plano",
  metricStatus: "Estado métrico del plano",
  revision: "Revisión",
  entitiesByLayer: "Entidades por capa",
  noEntities: "Sin entidades",
  events: "Eventos recientes",
  noEvents: "Sin eventos",
  decision: "Decisión",
  decisionHint:
    "Al validar, la revisión se congela. Para editar de nuevo hay que reabrirla, lo que crea una revisión nueva.",
  reasonLabel: "Motivo (obligatorio al rechazar)",
  submit: "Enviar a revisión",
  takeReview: "Tomar revisión",
  validate: "Validar",
  reject: "Rechazar",
  reopen: "Reabrir",
  transitionFailed: "No se pudo cambiar el estado del plano. Inténtalo de nuevo.",
  forbidden: "No tienes permiso para esta acción.",
  openEditor: "Abrir el editor",
  openReview: "Ir a la revisión",
  openExport: "Exportar",
  notEditable: "El plano está en solo lectura en su estado actual.",
  leaseLost: "Ya no tienes el permiso de edición de este plano. Recarga la página para volver a editar.",
  syncPending: "Cambios pendientes de sincronizar",
  syncDone: "Sincronizado",
  syncError: "Error al sincronizar los cambios",
} as const;

export const PLAN_STATUS_LABELS: Readonly<Record<PlanStatus, string>> = {
  CREATED: "Creado",
  CALIBRATION_PENDING: "Calibración pendiente",
  EDITING: "En edición",
  REVIEW_PENDING: "Pendiente de revisión",
  IN_REVIEW: "En revisión",
  VALIDATED: "Validado",
  EXPORTED: "Exportado",
  REJECTED: "Rechazado",
  REOPENED: "Reabierto",
};

export const METRIC_STATUS_LABELS: Readonly<Record<MetricStatus, string>> = {
  metric: "Métrico",
  approximate: "Aproximado",
  non_metric: "No métrico",
};

/** Statuses in which the plan's draft revision accepts edits (with the lease). */
export const EDITABLE_STATUSES: ReadonlySet<PlanStatus> = new Set(["EDITING", "IN_REVIEW"]);

/** `code` member of a problem response (`V-09`, `V-10`, `V-12`, `lease_required`, ...). */
export function problemCode(error: unknown): string | undefined {
  return error instanceof ApiError && typeof error.problem.code === "string"
    ? error.problem.code
    : undefined;
}

/** `holder` member of a V-12 problem: the user who holds the lease. */
export function problemHolder(error: unknown): LeaseOut | null {
  if (!(error instanceof ApiError)) return null;
  const holder = error.problem.holder as Partial<LeaseOut> | undefined;
  return typeof holder?.user_id === "string" &&
    typeof holder.display_name === "string" &&
    typeof holder.expires_at === "string"
    ? { user_id: holder.user_id, display_name: holder.display_name, expires_at: holder.expires_at }
    : null;
}

/** Spanish message of an API error of a plan action; `fallback` for anything unexpected. */
export function planErrorMessage(error: unknown, fallback: string): string {
  const holder = problemHolder(error);
  if (holder !== null) return v12Message(holder.display_name);
  const code = problemCode(error);
  if (code === "V-10") return V10_MESSAGE;
  if (code === "V-09") {
    return error instanceof ApiError && error.status === 422
      ? V09_REASON_MESSAGE
      : V09_VALIDATE_MESSAGE;
  }
  if (error instanceof ApiError && error.status === 403) return PLAN_MESSAGES.forbidden;
  return fallback;
}

type EntityType = components["schemas"]["EntityType"];

/** Spanish names of the entity types (metric status panel). */
export const ENTITY_TYPE_LABELS: Readonly<Record<EntityType, string>> = {
  line: "Línea",
  polyline: "Polilínea",
  point: "Punto",
  text: "Texto",
  dimension: "Cota",
  symbol: "Símbolo",
};

/** Spanish UI strings of the metric status badge and its panel (US-11, REQ-07). */
export const METRIC_PANEL_MESSAGES = {
  badgePrefix: "Estado métrico",
  loading: "Calculando…",
  loadFailed: "No se pudo obtener el estado métrico.",
  panelTitle: "Estado métrico del plano",
  metric: "Todas las entidades estructurales y los indicios tienen origen métrico.",
  nonMetric: "No métrico: sin calibración.",
  blocking: "Entidades que impiden el estado métrico",
  noBlocking: "Ninguna entidad impide el estado métrico.",
  discrepant: "Cotas con discrepancia (informativo)",
  discrepantItem: "El valor declarado no coincide con el medido",
  lengthConflicts: "Longitudes declaradas que dejaron de cumplirse (revisar)",
  lengthConflictItem:
    "Fijar longitud en una entidad conectada cambió su longitud declarada: vuelve a fijarla",
  unknownEntity: "Entidad no cargada",
} as const;

/** Why a blocking entity is not metric, by the reason code of `GET /plans/{id}/metric-status`. */
export const METRIC_REASON_LABELS: Readonly<Record<string, string>> = {
  trace_only: "Solo trazo: falta una cota o una calibración métrica",
  none: "Sin origen métrico: colocada a mano sin cota",
};

/** Explanation of `attributes.constraint_error` of an entity whose constraint failed. */
export const CONSTRAINT_ERROR_LABELS: Readonly<Record<string, string>> = {
  "V-06": "Las distancias ya no se cruzan: revisa las medidas de la triangulación.",
  reference_missing: "Falta una de las referencias de la restricción.",
  degenerate_baseline: "Los dos puntos de la línea base coinciden.",
  invalid_constraint: "La restricción guardada no es válida.",
};
