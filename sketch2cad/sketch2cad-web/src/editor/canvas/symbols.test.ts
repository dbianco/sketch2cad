import { describe, expect, it } from "vitest";
import {
  CSS_PX_PER_MM,
  symbolCanvasSize,
  symbolRenderSize,
  symbolSizeInput,
  symbolWorldSize,
  type SymbolSizeInput,
} from "./symbols";

const DOOR: SymbolSizeInput = { sizeMode: "world_m", defaultSize: 0.9, scale: 1, paperScale: 100 };
const MARKER: SymbolSizeInput = { sizeMode: "paper_mm", defaultSize: 6, scale: 1, paperScale: 100 };

describe("symbolRenderSize", () => {
  it.each([0.5, 20, 333])("doubles a world_m symbol's width when the zoom doubles (%s px/m)", (zoom) => {
    const before = symbolRenderSize(DOOR, { scale: zoom });
    const after = symbolRenderSize(DOOR, { scale: zoom * 2 });
    expect(before).toBeCloseTo(0.9 * zoom, 9);
    expect(after).toBeCloseTo(before * 2, 9);
  });

  it.each([0.5, 20, 333])("keeps a paper_mm symbol's width when the zoom doubles (%s px/m)", (zoom) => {
    const before = symbolRenderSize(MARKER, { scale: zoom });
    const after = symbolRenderSize(MARKER, { scale: zoom * 2 });
    expect(after).toBe(before);
    // 6 mm on paper is drawn as 6 CSS millimetres, about 22.7 px.
    expect(before).toBeCloseTo(6 * CSS_PX_PER_MM, 9);
    expect(before).toBeCloseTo(22.677, 3);
  });

  it("multiplies by the entity scale in both modes and ignores the paper scale on screen", () => {
    expect(symbolRenderSize({ ...DOOR, scale: 2 }, { scale: 10 })).toBeCloseTo(18, 9);
    expect(symbolRenderSize({ ...MARKER, scale: 2 }, { scale: 10 })).toBeCloseTo(
      12 * CSS_PX_PER_MM,
      9,
    );
    expect(symbolRenderSize({ ...MARKER, paperScale: 500 }, { scale: 10 })).toBe(
      symbolRenderSize(MARKER, { scale: 10 }),
    );
  });
});

describe("symbolWorldSize", () => {
  it("maps paper_mm to world metres with the paper scale (DXF INSERT scale, spec 12.2)", () => {
    // 6 mm at 1:100 is 0.6 m; at 1:250, 1.5 m.
    expect(symbolWorldSize(MARKER)).toBeCloseTo(0.6, 9);
    expect(symbolWorldSize({ ...MARKER, paperScale: 250 })).toBeCloseTo(1.5, 9);
    expect(symbolWorldSize({ ...MARKER, scale: 2 })).toBeCloseTo(1.2, 9);
  });

  it("keeps world_m sizes in metres whatever the paper scale", () => {
    expect(symbolWorldSize(DOOR)).toBeCloseTo(0.9, 9);
    expect(symbolWorldSize({ ...DOOR, paperScale: 500, scale: 1.5 })).toBeCloseTo(1.35, 9);
  });
});

describe("symbolCanvasSize", () => {
  it("is the world size for world_m and shrinks with the zoom for paper_mm", () => {
    expect(symbolCanvasSize(DOOR, { scale: 40 })).toBeCloseTo(0.9, 9);
    const near = symbolCanvasSize(MARKER, { scale: 40 });
    const far = symbolCanvasSize(MARKER, { scale: 80 });
    expect(far).toBeCloseTo(near / 2, 9);
    expect(symbolCanvasSize(MARKER, { scale: 0 })).toBe(0);
  });
});

describe("symbolSizeInput", () => {
  it("reads size mode and default size from a catalog symbol", () => {
    expect(symbolSizeInput({ size_mode: "paper_mm", default_size: 15 }, 1.5, 200)).toEqual({
      sizeMode: "paper_mm",
      defaultSize: 15,
      scale: 1.5,
      paperScale: 200,
    });
  });
});
