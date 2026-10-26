import type { components } from "../../api/schema";

export type MetricOrigin = components["schemas"]["MetricOrigin"];

/** Spanish UI strings of the evidence table (US-10, REQ-08). */
export const EVIDENCE_MESSAGES = {
  title: "Tabla de indicios",
  loading: "Cargando indicios…",
  loadFailed: "No se pudieron cargar los indicios.",
  empty: "El expediente no tiene indicios.",
  number: "Nº",
  label: "Etiqueta",
  x: "X (m)",
  y: "Y (m)",
  origin: "Origen métrico",
  actions: "Acciones",
  unplaced: "sin ubicar",
  selectRow: (n: number) => `Seleccionar el indicio ${String(n)} en el plano`,
  editLabel: "Editar etiqueta",
  newLabel: (n: number) => `Etiqueta del indicio ${String(n)}`,
  save: "Guardar",
  cancel: "Cancelar",
  assign: "Asignar al elemento seleccionado",
  assignHint: "Selecciona un solo punto o símbolo del plano para asignarle un indicio.",
  addTitle: "Agregar indicio",
  addLabel: "Etiqueta del nuevo indicio",
  add: "Agregar indicio",
  labelRequired: "Escribe una etiqueta.",
  saveFailed: "No se pudo guardar el indicio. Inténtalo de nuevo.",
} as const;

export const METRIC_ORIGIN_LABELS: Readonly<Record<MetricOrigin, string>> = {
  declared_dimension: "Cota declarada",
  triangulation: "Triangulación",
  baseline_offset: "Base y offset",
  scaled_drawing: "Dibujo a escala",
  derived: "Derivado",
  trace_only: "Solo trazo",
  none: "Sin origen métrico",
};
