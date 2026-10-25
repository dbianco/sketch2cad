import type { CSSProperties } from "react";
import type { components } from "../../api/schema";
import { CALIBRATION_MESSAGES as M } from "./messages";

type ScaleOut = components["schemas"]["ScaleOut"];

export const fieldStyles = {
  label: { fontSize: 13, display: "flex", flexDirection: "column", gap: 4 },
  input: { height: 40, border: "1px solid #9AA8B5", borderRadius: 6, padding: "0 12px", fontSize: 14 },
  hint: { margin: 0, fontSize: 13, color: "#4A5966", lineHeight: 1.45 },
} satisfies Record<string, CSSProperties>;

const percentFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 2 });

/** Parse a positive decimal typed with a comma or a dot; `null` when empty or invalid. */
export function parseDecimal(text: string): number | null {
  const value = Number(text.trim().replace(",", "."));
  return text.trim() === "" || !Number.isFinite(value) ? null : value;
}

interface DecimalFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
}

/** Text field for a decimal value (accepts `210,0`); kept as text so typing is not disturbed. */
export function DecimalField({ label, value, onChange }: DecimalFieldProps) {
  return (
    <label style={fieldStyles.label}>
      {label}
      <input
        type="text"
        inputMode="decimal"
        value={value}
        style={fieldStyles.input}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

/** `scan_dpi`: the scale comes from the source DPI metadata; there is nothing to enter. */
export function DpiStep({ dpi }: { dpi: number | null }) {
  return <p style={fieldStyles.hint}>{dpi === null ? M.noDpi : `DPI del archivo: ${String(dpi)}. ${M.dpiHint}`}</p>;
}

interface SegmentStepProps {
  marked: number;
  segmentMm: string;
  onSegmentMm: (value: string) => void;
  scale: ScaleOut | null;
}

/** `scan_segment`: two clicked points and their distance on paper; reports the DPI discrepancy. */
export function SegmentStep({ marked, segmentMm, onSegmentMm, scale }: SegmentStepProps) {
  return (
    <>
      <p style={fieldStyles.hint}>
        {M.segmentHint} Puntos marcados: {marked} de 2.
      </p>
      <DecimalField label={M.segmentLabel} value={segmentMm} onChange={onSegmentMm} />
      {scale?.dpi_discrepancy_pct != null && (
        <p style={fieldStyles.hint}>
          Diferencia con los DPI del archivo ({String(scale.dpi)}): {percentFormat.format(scale.dpi_discrepancy_pct)} %
          {scale.within_tolerance === false ? " (fuera de la tolerancia)" : ""}.
        </p>
      )}
    </>
  );
}
