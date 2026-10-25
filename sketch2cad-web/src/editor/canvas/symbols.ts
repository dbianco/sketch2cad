// Sizes of catalog symbols (spec 12.2). Pure functions; the canvas, the symbol tool and later
// hit-testing and bounds code share them.
//
// `size_mode` decides what `default_size` measures:
// - `world_m`: real metres. The symbol is part of the drawing, so on screen it grows and shrinks
//   with the zoom like any line (a 4.5 m vehicle is 4.5 m long at any zoom).
// - `paper_mm`: millimetres on paper (annotative, the CAD paper-space convention). In the DXF and
//   PDF it measures `default_size * scale` mm at any print scale, which in model space is
//   `mm * paperScale / 1000` world metres (the INSERT scale of spec 12.2). In the editor it keeps
//   a constant screen size whatever the zoom, like a CAD annotation in the viewport: one paper mm
//   is drawn as one CSS millimetre (CSS_PX_PER_MM).
import type { components } from "../../api/schema";
import type { Viewport } from "./viewport";

export type CatalogSymbol = components["schemas"]["Symbol"];
export type SymbolSizeMode = CatalogSymbol["size_mode"];

/** CSS pixels per millimetre (CSS reference unit: 96 px per inch, 25.4 mm per inch). */
export const CSS_PX_PER_MM = 96 / 25.4;

export interface SymbolSizeInput {
  sizeMode: SymbolSizeMode;
  /** Catalog `default_size`: mm on paper (`paper_mm`) or metres (`world_m`). */
  defaultSize: number;
  /** Entity `scale` (1 = catalog size). */
  scale: number;
  /** Denominator of the plan's paper scale (100 for 1:100). */
  paperScale: number;
}

/** Size input of a catalog symbol drawn at entity `scale`. */
export function symbolSizeInput(
  symbol: Pick<CatalogSymbol, "size_mode" | "default_size">,
  scale: number,
  paperScale: number,
): SymbolSizeInput {
  return { sizeMode: symbol.size_mode, defaultSize: symbol.default_size, scale, paperScale };
}

/**
 * Size of the symbol in the drawing, in world metres: what the DXF INSERT and the PDF use.
 * `paper_mm` symbols map to `mm * paperScale / 1000` m.
 */
export function symbolWorldSize(input: SymbolSizeInput): number {
  const size = input.defaultSize * input.scale;
  return input.sizeMode === "paper_mm" ? (size * input.paperScale) / 1000 : size;
}

/**
 * Width in screen px of one block unit of the symbol at `viewport`: `world_m` symbols scale with
 * the zoom (`metres * px per metre`); `paper_mm` symbols do not (`mm * CSS_PX_PER_MM`).
 */
export function symbolRenderSize(input: SymbolSizeInput, viewport: Pick<Viewport, "scale">): number {
  const size = input.defaultSize * input.scale;
  return input.sizeMode === "paper_mm" ? size * CSS_PX_PER_MM : size * viewport.scale;
}

/**
 * World metres that one block unit of the symbol covers on the canvas at `viewport`: the
 * footprint for hit-testing and canvas bounds. Equal to `symbolWorldSize` for `world_m`; for
 * `paper_mm` it depends on the zoom, since the symbol keeps its screen size.
 */
export function symbolCanvasSize(input: SymbolSizeInput, viewport: Pick<Viewport, "scale">): number {
  return viewport.scale > 0 ? symbolRenderSize(input, viewport) / viewport.scale : 0;
}
