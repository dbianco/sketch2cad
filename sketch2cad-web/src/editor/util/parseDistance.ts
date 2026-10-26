// Distances typed by the user (V-04, PRD): the same rule as the server's
// `sketch2cad.geometry.metrics.parse_distance`, so that the editor rejects a value before sending
// it. One decimal comma or point, an optional leading "+", surrounding blanks ignored; no other
// sign, no exponent, no thousands separator, and only finite values greater than zero.

/** User-facing message of V-04, as the server sends it. */
export const DISTANCE_MESSAGE = "Ingresa una distancia numérica mayor que cero, por ejemplo 3,50.";

const DISTANCE_PATTERN = /^\+?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)$/;

/** Metres of `text` ("4,20", " 4.2 ", "12"), or `null` when it breaks V-04. */
export function parseDistance(text: string): number | null {
  const candidate = text.trim();
  if (!DISTANCE_PATTERN.test(candidate)) return null;
  const value = Number(candidate.replace(",", "."));
  return Number.isFinite(value) && value > 0 ? value : null;
}
