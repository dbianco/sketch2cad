// North arrow of the editor canvas (US-05, spec 11.1). The canvas shows local coordinates with the
// local +Y axis pointing up the screen, and `north_angle_deg` is counter-clockwise from local +Y,
// so the needle (drawn pointing up) turns by `rotate(-north)`: SVG rotations are clockwise on
// screen. It is an overlay of fixed pixel size in the top-right corner of the canvas, outside the
// zoomed viewBox, so it never scales with the zoom and never moves with the pan.
export const NORTH_ARROW_SIZE_PX = 48;
/** Distance from the canvas's top and right edges, in px. */
export const NORTH_ARROW_MARGIN_PX = 8;

const numberFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 2 });

export function northArrowLabel(northDeg: number): string {
  return `Flecha de Norte: ${numberFormat.format(northDeg)}° respecto al eje +Y local`;
}

/** `transform` of the needle for a North angle in degrees (counter-clockwise from local +Y). */
export function northArrowTransform(northDeg: number): string {
  return `rotate(${String(-northDeg + 0)})`;
}

export function NorthArrow({ northDeg }: { northDeg: number }) {
  const half = NORTH_ARROW_SIZE_PX / 2;
  return (
    <svg
      data-testid="north-arrow"
      role="img"
      aria-label={northArrowLabel(northDeg)}
      width={NORTH_ARROW_SIZE_PX}
      height={NORTH_ARROW_SIZE_PX}
      viewBox={`${String(-half)} ${String(-half)} ${String(NORTH_ARROW_SIZE_PX)} ${String(NORTH_ARROW_SIZE_PX)}`}
      style={{
        position: "absolute",
        top: NORTH_ARROW_MARGIN_PX,
        right: NORTH_ARROW_MARGIN_PX,
        pointerEvents: "none",
      }}
    >
      <circle r={half - 1} fill="#ffffffcc" stroke="#555" strokeWidth={1} />
      <g data-testid="north-arrow-needle" transform={northArrowTransform(northDeg)}>
        <path d="M0 -11 L6 10 L0 5 L-6 10 Z" fill="#222" />
        <text y={-13} fontSize={9} textAnchor="middle" fill="#222">
          N
        </text>
      </g>
    </svg>
  );
}
