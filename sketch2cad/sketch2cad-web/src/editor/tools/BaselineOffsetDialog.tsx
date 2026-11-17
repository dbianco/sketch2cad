// Baseline-offset tool (US-09, spec 11.3, V-07): pick the point to fix and the base points A and B
// (points, symbols or line vertices, even two of the same wall; ConstraintTool.tsx), type s along
// A→B (any sign: negative is behind A) and the perpendicular offset d (zero or more), and choose
// the side, left or right looking from A to B. The resulting position P = A + s·û + d·n̂ is drawn
// on the canvas before "Aplicar" sends `baseline_offset`.
import { formatMetres } from "../canvas/EditorCanvas";
import { baselineOffsetPoint, type Side } from "../geometry/preview";
import type { EditorTool, ToolContext } from "../ui/editorUi";
import { createConstraintTool, type ConstraintConfig, type FieldsProps } from "./ConstraintTool";
import { V07_MESSAGE } from "./TriangulationDialog";

export const BASELINE_TEXT = {
  title: "Base + offset",
  slots: ["Punto a fijar", "Punto base A", "Punto base B"] as const,
  along: "Distancia a lo largo de A→B, s (m)",
  alongHint: "Puede ser negativa (detrás de A), por ejemplo -1,50",
  offset: "Distancia perpendicular, d (m)",
  offsetHint: "Cero o más, por ejemplo 0,80",
  side: "Lado, mirando de A hacia B",
  left: "Izquierda",
  right: "Derecha",
  badAlong: "Ingresa la distancia a lo largo como número, por ejemplo 1,50 o -1,50.",
  badOffset:
    "Ingresa la distancia perpendicular como número mayor o igual que cero, por ejemplo 0,80.",
  sameBase: "Los dos puntos base están en la misma posición: la línea base no tiene dirección.",
  done: "Punto fijado por línea base y offset.",
  at: (x: number, y: number) => `Posición: x ${formatMetres(x)} · y ${formatMetres(y)}`,
} as const;

/** Same rule as the server's signed lengths: decimal comma or point, optional sign. */
const SIGNED_NUMBER = /^[+-]?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)$/;

/** Metres of `text`, of any sign, or null when it is not a number. */
export function parseSignedLength(text: string): number | null {
  const candidate = text.trim();
  if (!SIGNED_NUMBER.test(candidate)) return null;
  const value = Number(candidate.replace(",", "."));
  return Number.isFinite(value) ? value + 0 : null;
}

export interface BaselineValues {
  s: string;
  d: string;
  side: Side;
}

function parsed(values: BaselineValues): { s: number; d: number } | { error: string } {
  const s = parseSignedLength(values.s);
  const d = parseSignedLength(values.d);
  if (s === null) return { error: BASELINE_TEXT.badAlong };
  if (d === null || d < 0) return { error: BASELINE_TEXT.badOffset };
  return { s, d };
}

function BaselineFields({ values, setValues, preview, busy }: FieldsProps<BaselineValues>) {
  const at = preview?.candidates[0];
  return (
    <>
      <label style={{ display: "block" }}>
        {BASELINE_TEXT.along}{" "}
        <input
          type="text"
          inputMode="decimal"
          title={BASELINE_TEXT.alongHint}
          value={values.s}
          disabled={busy}
          onChange={(event) => setValues({ s: event.target.value })}
        />
      </label>
      <label style={{ display: "block" }}>
        {BASELINE_TEXT.offset}{" "}
        <input
          type="text"
          inputMode="decimal"
          title={BASELINE_TEXT.offsetHint}
          value={values.d}
          disabled={busy}
          onChange={(event) => setValues({ d: event.target.value })}
        />
      </label>
      <fieldset>
        <legend>{BASELINE_TEXT.side}</legend>
        {(["left", "right"] as const).map((side) => (
          <label key={side} style={{ display: "block" }}>
            <input
              type="radio"
              name="baseline-side"
              checked={values.side === side}
              disabled={busy}
              onChange={() => setValues({ side })}
            />{" "}
            {BASELINE_TEXT[side]}
          </label>
        ))}
      </fieldset>
      {at !== undefined && <p>{BASELINE_TEXT.at(at[0], at[1])}</p>}
    </>
  );
}

export const BASELINE_CONFIG: ConstraintConfig<BaselineValues> = {
  title: BASELINE_TEXT.title,
  slots: BASELINE_TEXT.slots,
  initial: { s: "", d: "", side: "left" },
  preview([, a, b], values) {
    const numbers = parsed(values);
    if ("error" in numbers) return null;
    const point = baselineOffsetPoint(a, b, numbers.s, numbers.d, values.side);
    if (point === null) {
      return { candidates: [], selected: null, nearest: null, problem: BASELINE_TEXT.sameBase };
    }
    return { candidates: [point], selected: 0, nearest: null, problem: null };
  },
  request([baseA, baseB], values, preview) {
    const numbers = parsed(values);
    if ("error" in numbers) return numbers;
    if (preview?.problem != null) return { error: preview.problem };
    return {
      operation: "baseline_offset",
      base_a: baseA,
      base_b: baseB,
      s_m: numbers.s,
      d_m: numbers.d,
      side: values.side,
    };
  },
  messages: { "V-07": V07_MESSAGE },
  done: BASELINE_TEXT.done,
  drawBaseline: true,
  Fields: BaselineFields,
};

export function createBaselineOffsetTool(context: ToolContext): EditorTool {
  return createConstraintTool(context, BASELINE_CONFIG);
}
