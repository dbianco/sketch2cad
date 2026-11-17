import { CALIBRATION_MESSAGES as M, CORNER_LABELS } from "./messages";
import { DecimalField, fieldStyles } from "./ScanSteps";

/** A check point: clicked pixel plus its typed position in the pattern frame (mm). */
export interface CheckPointDraft {
  px: [number, number];
  x: string;
  y: string;
}

interface FourPointStepProps {
  marked: number;
  width: string;
  height: string;
  onWidth: (value: string) => void;
  onHeight: (value: string) => void;
  checks: readonly CheckPointDraft[];
  onCheckChange: (index: number, field: "x" | "y", value: string) => void;
  onRemoveCheck: (index: number) => void;
  markingCheck: boolean;
  onAddCheck: () => void;
}

/**
 * `photo_homography_4pt`: four corners clicked in order TL, TR, BR, BL plus the pattern size,
 * and optional check points (spec 11.2) that measure the real reprojection error.
 */
export function FourPointStep(props: FourPointStepProps) {
  const { marked, checks } = props;
  return (
    <>
      <div style={fieldStyles.hint}>
        {M.cornersHint}
        <ol aria-label="Esquinas" style={{ margin: "4px 0 0", paddingLeft: 20 }}>
          {CORNER_LABELS.map((corner, index) => (
            <li key={corner} style={index < marked ? { color: "#145A32", fontWeight: 600 } : undefined}>
              {corner}
              {index < marked ? " ✓" : ""}
            </li>
          ))}
        </ol>
      </div>
      <DecimalField label={M.widthLabel} value={props.width} onChange={props.onWidth} />
      <DecimalField label={M.heightLabel} value={props.height} onChange={props.onHeight} />
      <p style={fieldStyles.hint}>{M.checkHint}</p>
      {checks.map((check, index) => (
        <fieldset key={index} style={{ border: "1px solid #D5DCE3", borderRadius: 6, display: "flex", gap: 8 }}>
          <legend style={{ fontSize: 13 }}>Punto de control {index + 1}</legend>
          <DecimalField label={M.checkX} value={check.x} onChange={(value) => props.onCheckChange(index, "x", value)} />
          <DecimalField label={M.checkY} value={check.y} onChange={(value) => props.onCheckChange(index, "y", value)} />
          <button
            type="button"
            aria-label={`${M.removeCheck} el punto de control ${String(index + 1)}`}
            onClick={() => props.onRemoveCheck(index)}
          >
            {M.removeCheck}
          </button>
        </fieldset>
      ))}
      <button type="button" disabled={marked < 4 || props.markingCheck} onClick={props.onAddCheck}>
        {M.addCheck}
      </button>
      {props.markingCheck && <p style={fieldStyles.hint}>{M.clickToMark}</p>}
    </>
  );
}
