// Keyboard shortcuts of the editor (spec 5 WCAG 2.1 AA, jotdown H2-14): the single source of the
// keys that the palette, the keyboard cursor (keyboardCursor.tsx), the calibration viewer and the
// keyboard help (KeyboardHelp.tsx) use. Pure data and helpers, no React.
import type { ToolId } from "../ui/editorUi";

/** Shortcut key of each tool (upper case; the lower-case key works too). */
export const TOOL_SHORTCUTS: Readonly<Record<ToolId, string>> = {
  select: "V",
  vertex: "E",
  line: "L",
  polyline: "P",
  point: "O",
  text: "T",
  symbol: "S",
  frame: "F",
  fixlength: "D",
  triangulate: "G",
  baseline: "B",
  ocr: "R",
};

/** Tools of the AI assistance: offered only while `ai_assist.enabled` is on (H5-7). */
export const AI_TOOLS: ReadonlySet<ToolId> = new Set<ToolId>(["ocr"]);

/** Tools that place points on the canvas, so the keyboard cursor drives them. */
export const CURSOR_TOOLS: ReadonlySet<ToolId> = new Set<ToolId>([
  "line",
  "polyline",
  "point",
  "text",
  "symbol",
  "frame",
  "fixlength",
  "triangulate",
  "baseline",
  "ocr",
]);

/** Key that opens the keyboard help. */
export const HELP_KEY = "?";

/** Keys that act as a primary click at the keyboard cursor. */
export const PLACE_KEYS: ReadonlySet<string> = new Set(["Enter", " "]);

/**
 * Step multipliers of the cursor: Shift moves a tenth of the step (the same fine factor as the
 * vertex nudge of VertexEditTool), Alt or Ctrl ten steps.
 */
export const FINE_STEP_FACTOR = 0.1;
export const COARSE_STEP_FACTOR = 10;

/** Screen direction (x right, y down) of an arrow key, or null for any other key. */
export function arrowDirection(key: string): [number, number] | null {
  switch (key) {
    case "ArrowLeft":
      return [-1, 0];
    case "ArrowRight":
      return [1, 0];
    case "ArrowUp":
      return [0, -1];
    case "ArrowDown":
      return [0, 1];
    default:
      return null;
  }
}

export interface ModifierState {
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
}

/** Multiplier of the cursor step for the modifiers held. */
export function stepMultiplier(event: ModifierState): number {
  if (event.altKey || event.ctrlKey) return COARSE_STEP_FACTOR;
  return event.shiftKey ? FINE_STEP_FACTOR : 1;
}

/**
 * Screen displacement (x right, y down) of an arrow key press for a base `step`, or null when
 * the key is not an arrow.
 */
export function cursorDelta(
  event: ModifierState & { key: string },
  step: number,
): [number, number] | null {
  const direction = arrowDirection(event.key);
  if (direction === null) return null;
  const distance = step * stepMultiplier(event);
  return [direction[0] * distance, direction[1] * distance];
}

export interface ShortcutEntry {
  /** Keys as shown to the user. */
  keys: string;
  description: string;
}

export interface ShortcutGroup {
  title: string;
  entries: readonly ShortcutEntry[];
}

const TOOL_NAMES: Readonly<Record<ToolId, string>> = {
  select: "Seleccionar",
  vertex: "Editar vértices",
  line: "Línea",
  polyline: "Polilínea",
  point: "Punto",
  text: "Texto",
  symbol: "Símbolo",
  frame: "Sistema local",
  fixlength: "Fijar longitud",
  triangulate: "Triangulación",
  baseline: "Base + offset",
  ocr: "Leer cota (OCR)",
};

/** Everything the keyboard help lists, in Spanish. */
export const SHORTCUT_GROUPS: readonly ShortcutGroup[] = [
  {
    title: "Herramientas",
    // The AI tools are left out: the help does not know whether AI assistance is on.
    entries: (Object.keys(TOOL_SHORTCUTS) as ToolId[])
      .filter((tool) => !AI_TOOLS.has(tool))
      .map((tool) => ({
        keys: TOOL_SHORTCUTS[tool],
        description: TOOL_NAMES[tool],
      })),
  },
  {
    title: "Cursor de teclado (lienzo con el foco y herramienta de dibujo activa)",
    entries: [
      { keys: "Flechas", description: "Mover el cursor un paso de la grilla" },
      { keys: "Mayús + flechas", description: "Mover el cursor una décima de paso" },
      { keys: "Alt o Ctrl + flechas", description: "Mover el cursor diez pasos" },
      { keys: "Intro o Espacio", description: "Hacer clic en el cursor (marcar un punto)" },
      { keys: "Esc", description: "Cancelar el dibujo en curso" },
    ],
  },
  {
    title: "Dibujo",
    entries: [
      { keys: "Línea: Intro, flechas, Intro", description: "Crear una línea del primer al segundo punto" },
      { keys: "Polilínea: Espacio", description: "Añadir un vértice en el cursor" },
      {
        keys: "Polilínea: Intro",
        description: "Terminar la polilínea abierta (con menos de 2 vértices añade uno)",
      },
      { keys: "Polilínea: Mayús + Espacio", description: "Añadir el último vértice y cerrar" },
      { keys: "Polilínea: C", description: "Cerrar con los vértices marcados" },
      { keys: "Polilínea: Retroceso", description: "Quitar el último vértice" },
      { keys: "Texto: Intro", description: "Colocar el texto; escribir y pulsar Intro para crearlo" },
      {
        keys: "Sistema local: Intro, flechas, Intro",
        description: "Marcar el origen y después un punto del eje +X",
      },
      { keys: "Seleccionar: Supr o Retroceso", description: "Borrar la selección" },
      { keys: "Vértices: Tab y flechas", description: "Elegir un vértice y moverlo" },
      {
        keys: "Fijar longitud: Intro, Intro",
        description: "Elegir el segmento y después su vértice ancla (o Tab hasta el extremo)",
      },
      { keys: "Fijar longitud: Esc", description: "Cancelar el segmento elegido" },
      {
        keys: "Triangulación y Base + offset: Intro",
        description:
          "Elegir el punto a fijar y después las referencias A y B (puntos, símbolos o esquinas de líneas)",
      },
      { keys: "Triangulación y Base + offset: Esc", description: "Quitar los puntos elegidos" },
    ],
  },
  {
    title: "Vista y general",
    entries: [
      { keys: "Flechas (sin herramienta de dibujo)", description: "Desplazar la vista" },
      { keys: "+ o -", description: "Acercar o alejar la vista" },
      { keys: "Espacio + arrastrar", description: "Desplazar la vista con el ratón" },
      { keys: "Ctrl o Cmd + Z", description: "Deshacer" },
      { keys: "Mayús + Ctrl o Cmd + Z", description: "Rehacer" },
      { keys: HELP_KEY, description: "Abrir esta ayuda" },
    ],
  },
];
