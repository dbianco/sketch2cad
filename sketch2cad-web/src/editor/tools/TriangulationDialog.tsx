// Triangulation tool (US-08, spec 11.3, V-04, V-06, V-07): pick the point to fix and the
// references A and B (ConstraintTool.tsx), type dA and dB (decimal comma or point). Both circle
// intersections are drawn on the canvas with the one nearest to the point's current position
// preselected; the user may take the other one (radio or a click on its marker). The server's
// `solution` is relative to that same position: the nearest is sent as "nearest", the other one as
// "alt".
import { formatMetres } from "../canvas/EditorCanvas";
import { triangulate, type Solution } from "../geometry/preview";
import type { EditorTool, ToolContext } from "../ui/editorUi";
import { DISTANCE_MESSAGE, parseDistance } from "../util/parseDistance";
import { createConstraintTool, type ConstraintConfig, type FieldsProps } from "./ConstraintTool";

/** PRD messages of V-06 and V-07, as the server sends them. */
export const V06_MESSAGE =
  "Estas dos distancias no se cruzan. Revisa las medidas o las referencias elegidas.";
export const V07_MESSAGE = "Esta referencia no es métrica o depende del punto que intentas fijar.";

export const TRIANGULATION_TEXT = {
  title: "Triangulación",
  distanceA: "Distancia a A (m)",
  distanceB: "Distancia a B (m)",
  hint: "Coma o punto decimal, por ejemplo 3,50",
  solution: "Solución",
  nearest: "La más cercana a lo dibujado",
  alt: "La otra solución",
  two: "Dos soluciones. Se preselecciona la más cercana a lo dibujado.",
  one: "Una sola solución: las dos circunferencias se tocan en un punto.",
  done: "Punto fijado por triangulación.",
  at: (x: number, y: number) => `Posición: x ${formatMetres(x)} · y ${formatMetres(y)}`,
} as const;

export interface TriangulationValues {
  dA: string;
  dB: string;
  /** Candidate relative to the point's current position. */
  choice: Solution;
}

function TriangulationFields({
  values,
  setValues,
  preview,
  busy,
}: FieldsProps<TriangulationValues>) {
  const selected = preview?.selected == null ? undefined : preview.candidates[preview.selected];
  return (
    <>
      <label style={{ display: "block" }}>
        {TRIANGULATION_TEXT.distanceA}{" "}
        <input
          type="text"
          inputMode="decimal"
          title={TRIANGULATION_TEXT.hint}
          value={values.dA}
          disabled={busy}
          onChange={(event) => setValues({ dA: event.target.value })}
        />
      </label>
      <label style={{ display: "block" }}>
        {TRIANGULATION_TEXT.distanceB}{" "}
        <input
          type="text"
          inputMode="decimal"
          title={TRIANGULATION_TEXT.hint}
          value={values.dB}
          disabled={busy}
          onChange={(event) => setValues({ dB: event.target.value })}
        />
      </label>
      {preview?.candidates.length === 2 && (
        <fieldset>
          <legend>{TRIANGULATION_TEXT.solution}</legend>
          <p style={{ margin: 0 }}>{TRIANGULATION_TEXT.two}</p>
          {(["nearest", "alt"] as const).map((choice) => (
            <label key={choice} style={{ display: "block" }}>
              <input
                type="radio"
                name="triangulation-solution"
                checked={values.choice === choice}
                disabled={busy}
                onChange={() => setValues({ choice })}
              />{" "}
              {TRIANGULATION_TEXT[choice]}
            </label>
          ))}
        </fieldset>
      )}
      {preview?.candidates.length === 1 && <p>{TRIANGULATION_TEXT.one}</p>}
      {selected !== undefined && <p>{TRIANGULATION_TEXT.at(selected[0], selected[1])}</p>}
    </>
  );
}

export const TRIANGULATION_CONFIG: ConstraintConfig<TriangulationValues> = {
  title: TRIANGULATION_TEXT.title,
  initial: { dA: "", dB: "", choice: "nearest" },
  preview([target, a, b], values) {
    const dA = parseDistance(values.dA);
    const dB = parseDistance(values.dB);
    if (dA === null || dB === null) return null;
    const result = triangulate(a, b, dA, dB, values.choice, target);
    if (result.point === null) {
      return { candidates: [], selected: null, nearest: null, problem: V06_MESSAGE };
    }
    return {
      candidates: result.candidates,
      selected: result.candidates.indexOf(result.point),
      nearest: result.nearestIndex,
      problem: null,
    };
  },
  request([refA, refB], values, preview) {
    const dA = parseDistance(values.dA);
    const dB = parseDistance(values.dB);
    if (dA === null || dB === null) return { error: DISTANCE_MESSAGE };
    if (preview?.problem != null) return { error: preview.problem };
    return {
      operation: "triangulate",
      ref_a: refA,
      ref_b: refB,
      d_a_m: dA,
      d_b_m: dB,
      solution: preview?.candidates.length === 1 ? "nearest" : values.choice,
    };
  },
  messages: { "V-06": V06_MESSAGE, "V-07": V07_MESSAGE },
  done: TRIANGULATION_TEXT.done,
  choose(values, index, preview) {
    if (preview.candidates.length !== 2) return null;
    return { ...values, choice: index === preview.nearest ? "nearest" : "alt" };
  },
  Fields: TriangulationFields,
};

export function createTriangulationTool(context: ToolContext): EditorTool {
  return createConstraintTool(context, TRIANGULATION_CONFIG);
}
