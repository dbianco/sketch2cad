import type { components } from "../../api/schema";

export type CalibrationMethod = components["schemas"]["CalibrationMethod"];
export type SourceMode = components["schemas"]["SourceMode"];

/** Spanish UI strings of the calibration screens. */
export const CALIBRATION_MESSAGES = {
  title: "Calibración",
  sourceModeStep: "Paso 1 de 2 · Modo de fuente",
  methodStep: "Paso 2 de 2 · Método",
  sourceModeLabel: "Modo de fuente",
  planTitleLabel: "Título del plano",
  defaultPlanTitle: "Plano 1",
  createPlan: "Crear plano y continuar",
  methodLabel: "Método",
  paperScaleLabel: "Escala del dibujo (1:N)",
  propose: "Calcular calibración",
  acceptMetric: "Aceptar como métrica",
  acceptApproximate: "Continuar como aproximado",
  resetPoints: "Repetir puntos",
  loading: "Cargando…",
  loadFailed: "No se pudo cargar el plano o la fuente. Recarga la página para reintentar.",
  createFailed: "No se pudo crear el plano. Inténtalo de nuevo.",
  proposeFailed: "No se pudo calcular la calibración con estos datos. Revisa los puntos y las medidas.",
  acceptFailed: "No se pudo aceptar la calibración. Inténtalo de nuevo.",
  outdated: "Los datos cambiaron: vuelve a calcular la calibración.",
  noMetricNone: "Sin calibración el plano queda no métrico.",
  noMetricPoints: "Marca las cuatro esquinas para aceptar la calibración como métrica.",
  noMetricSegment: "Marca los dos extremos del segmento e indica su longitud.",
  noMetricDimensions: "Indica el ancho y el alto del patrón.",
  noMetricProposal: "Calcula la calibración antes de aceptarla como métrica.",
  noMetricPaperScale: "Indica la escala del dibujo para aceptarlo como métrico.",
  noneHint: "El plano se editará sin escala: quedará como no métrico.",
  dpiHint: "La escala sale de los DPI del archivo; depende de que el metadato sea correcto.",
  noDpi: "El archivo no tiene DPI: usa el escaneo con segmento.",
  segmentHint: "Marca en la imagen los dos extremos de un segmento de longitud conocida.",
  segmentLabel: "Longitud del segmento en el papel (mm)",
  cornersHint: "Marca las 4 esquinas del patrón de referencia en este orden:",
  widthLabel: "Ancho del patrón (mm)",
  heightLabel: "Alto del patrón (mm)",
  checkHint:
    "Con 4 esquinas el ajuste es exacto y el error sale casi cero. Marca puntos de control " +
    "(una marca de regla o un punto de posición conocida) para medir el error real.",
  addCheck: "Marcar punto de control",
  checkX: "X en el patrón (mm)",
  checkY: "Y en el patrón (mm)",
  removeCheck: "Quitar",
  clickToMark: "Haz clic en la imagen para marcar el punto.",
  withinTolerance: (max: number) => `Dentro del límite de ${formatMm(max)}`,
  basisCheck: "Medido en los puntos de control.",
  basisControl: "Medido en las esquinas: marca puntos de control para medir el error real.",
  editorTitle: "Editor del plano",
  editorPending: "El editor se monta en esta ruta en una tarea posterior.",
} as const;

export const SOURCE_MODE_LABELS: Readonly<Record<SourceMode, string>> = {
  schematic_sketch: "Croquis esquemático",
  scaled_drawing: "Dibujo a escala",
};

export const METHOD_LABELS: Readonly<Record<CalibrationMethod, string>> = {
  photo_homography_4pt: "Fotografía, 4 puntos",
  scan_dpi: "Escaneo con DPI",
  scan_segment: "Escaneo con segmento",
  none: "Sin calibración",
};

/** Corner names of the 4-point method, in marking order (TL, TR, BR, BL). */
export const CORNER_LABELS = [
  "superior izquierda",
  "superior derecha",
  "inferior derecha",
  "inferior izquierda",
] as const;

const mmFormat = new Intl.NumberFormat("es", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** A length in millimetres with one decimal and a decimal comma: `1,3 mm`. */
export function formatMm(value: number): string {
  return `${mmFormat.format(value)} mm`;
}

/** V-05: the reprojection error is above the maximum for a metric calibration. */
export function v05Message(errorMm: number, maximumMm: number): string {
  return (
    `El error de calibración es de ${formatMm(errorMm)} y supera el máximo de ${formatMm(maximumMm)}. ` +
    "Repite los puntos o continúa como aproximado."
  );
}

/** V-12: another user holds the plan's edit lease. */
export function v12Message(holder: string): string {
  return `${holder} está editando este plano. Entra en solo lectura o espera a que termine.`;
}
