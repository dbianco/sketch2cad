import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createEditorStore } from "../store/entities";
import type { EntityOut, Geometry } from "../store/types";
import {
  CANVAS_TEXT,
  EditorCanvas,
  SELECTION_COLOR,
  WHEEL_ZOOM_PER_PX,
  type Catalog,
} from "./EditorCanvas";
import { parseSymbolSvg } from "./symbolArt";
import { CSS_PX_PER_MM } from "./symbols";
import type { Viewport } from "./viewport";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
const IMAGE_URL = "blob:http://localhost/rectified-1";
const MARKER_SVG =
  "<svg xmlns='http://www.w3.org/2000/svg' viewBox='-50 -50 100 100'>" +
  "<g fill='none' stroke='currentColor' stroke-width='4'><circle cx='0' cy='0' r='40'/></g></svg>";

const DOOR_SVG =
  "<svg xmlns='http://www.w3.org/2000/svg' viewBox='-5 -105 110 110'>" +
  "<g fill='none' stroke='currentColor'><line x1='0' y1='0' x2='0' y2='-100'/></g></svg>";

const CATALOG: Catalog = {
  id: "00000000-0000-4000-8000-0000000000c1",
  version: "default-1",
  layers: [
    { name: "L_STRUCTURE", description: "Muros", aci_color: 7 },
    { name: "L_EVIDENCE", description: "Indicios", aci_color: 1 },
    { name: "L_TEXT", description: "Etiquetas", aci_color: 7 },
  ],
  symbols: [
    {
      symbol_id: "evidence_marker",
      label: "Marcador de indicio",
      default_layer: "L_EVIDENCE",
      svg: MARKER_SVG,
      dxf_block: { name: "S2C_evidence_marker", base_point: [0, 0], entities: [] },
      size_mode: "paper_mm",
      default_size: 6,
      attributes: [],
    },
    {
      symbol_id: "door",
      label: "Puerta",
      default_layer: "L_STRUCTURE",
      svg: DOOR_SVG,
      dxf_block: { name: "S2C_door", base_point: [0, 0], entities: [] },
      size_mode: "world_m",
      default_size: 0.9,
      attributes: [],
    },
  ],
};

function entityOut(id: string, layer: string, geometry: Geometry): EntityOut {
  return {
    id,
    type: geometry.kind,
    layer,
    geometry,
    attributes: {},
    confidence: null,
    constraint: null,
    created_by: null,
    deleted_at: null,
    discrepancy: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "none",
    model_version: null,
    review_status: "accepted",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "manual" },
    updated_at: "2026-10-25T00:00:00Z",
  };
}

const ENTITIES: EntityOut[] = [
  entityOut("wall", "L_STRUCTURE", {
    kind: "polyline",
    closed: true,
    coords_m: [
      [0, 0],
      [10, 0],
      [10, 8],
    ],
  }),
  entityOut("evidence-point", "L_EVIDENCE", { kind: "point", coords_m: [[2, 3]] }),
  entityOut("marker", "L_EVIDENCE", {
    kind: "symbol",
    symbol_id: "evidence_marker",
    anchor_m: [4, 4],
    rotation_deg: 0,
    scale: 1,
  }),
  entityOut("door", "L_STRUCTURE", {
    kind: "symbol",
    symbol_id: "door",
    anchor_m: [6, 2],
    rotation_deg: 90,
    scale: 1,
  }),
  entityOut("label", "L_TEXT", {
    kind: "text",
    anchor_m: [1, 1],
    content: "Cocina",
    height_mm_paper: 2.5,
    rotation_deg: 0,
  }),
  entityOut("dim", "L_STRUCTURE", {
    kind: "dimension",
    p1_m: [0, 0],
    p2_m: [10, 0],
    offset_m: 1,
    declared_value_m: 10.05,
    measured_value_m: 10,
  }),
];

function renderCanvas() {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, ENTITIES);
  const camera: { current: Viewport | null } = { current: null };
  const result = render(
    <EditorCanvas
      catalog={CATALOG}
      store={store}
      width={800}
      height={600}
      imageUrl={IMAGE_URL}
      imageBounds={{ minX: 0, minY: 0, maxX: 20, maxY: 15 }}
      onViewportChange={(viewport) => {
        camera.current = viewport;
      }}
    />,
  );
  /** Zoom (px per metre) of the last render. */
  const zoom = (): number => {
    if (camera.current === null) throw new Error("No viewport yet");
    return camera.current.scale;
  };
  return { store, zoom, ...result };
}

function canvas(): SVGSVGElement {
  return screen.getByTestId("editor-canvas") as unknown as SVGSVGElement;
}

function layerGroup(container: ParentNode, name: string): SVGGElement {
  const group = container.querySelector<SVGGElement>(`g[data-layer="${name}"]`);
  if (group === null) throw new Error(`No group for layer ${name}`);
  return group;
}

function entityElement(container: ParentNode, id: string): Element | null {
  return container.querySelector(`[data-entity-id="${id}"]`);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EditorCanvas layers", () => {
  it("renders each entity inside the group of its layer, in catalog order", () => {
    const { container } = renderCanvas();
    const layers = [...container.querySelectorAll("g[data-layer]")].map((g) =>
      g.getAttribute("data-layer"),
    );
    expect(layers).toEqual(["L_STRUCTURE", "L_EVIDENCE", "L_TEXT"]);
    expect(layerGroup(container, "L_EVIDENCE").getAttribute("color")).toBe("#e00000");
    expect(entityElement(layerGroup(container, "L_STRUCTURE"), "wall")?.tagName).toBe("polygon");
    expect(entityElement(container, "evidence-point")?.getAttribute("vector-effect")).toBe(
      "non-scaling-stroke",
    );
    expect(entityElement(container, "label")?.textContent).toBe("Cocina");
    // Text height is paper mm at the plan scale: 2.5 mm at 1:100 is 0.25 m.
    expect(entityElement(container, "label")?.getAttribute("font-size")).toBe("0.25");
    // The declared value; 10.05 vs 10.00 measured is beyond the 0.02 m tolerance, so the
    // measured length follows with a warning.
    expect(entityElement(container, "dim")?.querySelector("text")?.textContent).toBe("10,05 m");
    expect(entityElement(container, "dim")?.getAttribute("data-discrepancy")).toBe("true");
  });

  it("hides the elements of a layer when its visibility is toggled off, and shows them again", () => {
    const { container } = renderCanvas();
    const toggle = screen.getByRole<HTMLInputElement>("checkbox", {
      name: CANVAS_TEXT.show("L_EVIDENCE"),
    });
    expect(toggle.checked).toBe(true);
    expect(entityElement(container, "evidence-point")).not.toBeNull();

    fireEvent.click(toggle);

    expect(toggle.checked).toBe(false);
    const group = layerGroup(container, "L_EVIDENCE");
    expect(group.getAttribute("display")).toBe("none");
    expect(entityElement(container, "evidence-point")).toBeNull();
    expect(entityElement(container, "marker")).toBeNull();
    expect(entityElement(container, "wall")).not.toBeNull();
    expect(layerGroup(container, "L_STRUCTURE").getAttribute("display")).toBeNull();

    fireEvent.click(toggle);

    expect(layerGroup(container, "L_EVIDENCE").getAttribute("display")).toBeNull();
    expect(entityElement(container, "evidence-point")).not.toBeNull();
  });

  it("disables pointer events on a locked layer", () => {
    const { container } = renderCanvas();
    fireEvent.click(screen.getByRole("checkbox", { name: CANVAS_TEXT.lock("L_STRUCTURE") }));
    expect(layerGroup(container, "L_STRUCTURE").getAttribute("pointer-events")).toBe("none");
    expect(layerGroup(container, "L_TEXT").getAttribute("pointer-events")).toBeNull();
  });
});

describe("EditorCanvas source image", () => {
  it("draws the image beneath the entities and the slider sets its opacity between 0 and 1", () => {
    const { container } = renderCanvas();
    const image = screen.getByTestId("source-image");
    expect(canvas().firstElementChild).toBe(image);
    expect(image.getAttribute("href")).toBe(IMAGE_URL);
    // World Y up: the image's top edge (maxY = 15) is at SVG y = -15.
    expect(image.getAttribute("y")).toBe("-15");
    const slider = screen.getByRole("slider", { name: CANVAS_TEXT.opacity });

    for (const value of ["0", "0.35", "1"]) {
      fireEvent.change(slider, { target: { value } });
      expect(image.getAttribute("opacity")).toBe(value);
    }
    expect(container.querySelector("image")).toBe(image);
  });
});

describe("EditorCanvas camera", () => {
  function expectNoCssTransform(container: HTMLElement): void {
    for (const element of [container, ...container.querySelectorAll("*")]) {
      expect((element as HTMLElement).style?.transform ?? "").toBe("");
    }
  }

  it("zooms with the wheel and pans with a background drag by changing only the viewBox", () => {
    const { container } = renderCanvas();
    const svg = canvas();
    const wall = entityElement(container, "wall");
    const initial = svg.getAttribute("viewBox");

    fireEvent.wheel(svg, { deltaY: -200, clientX: 100, clientY: 100 });
    const zoomed = svg.getAttribute("viewBox");
    expect(zoomed).not.toBe(initial);
    const [, , zoomedWidth] = (zoomed ?? "").split(" ").map(Number);
    const [, , initialWidth] = (initial ?? "").split(" ").map(Number);
    expect(zoomedWidth).toBeLessThan(initialWidth ?? 0);

    fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: 300, clientY: 300 });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 350, clientY: 280 });
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: 350, clientY: 280 });
    const panned = svg.getAttribute("viewBox");
    expect(panned).not.toBe(zoomed);
    const [pannedX, , pannedWidth] = (panned ?? "").split(" ").map(Number);
    const [zoomedX] = (zoomed ?? "").split(" ").map(Number);
    expect(pannedWidth).toBe(zoomedWidth);
    expect(pannedX).toBeLessThan(zoomedX ?? 0);

    // Entity elements are kept, not re-created, and nothing got a CSS transform.
    expect(entityElement(container, "wall")).toBe(wall);
    expectNoCssTransform(container);
  });

  it("selects an entity on pointer down without panning", () => {
    const { container, store } = renderCanvas();
    const before = canvas().getAttribute("viewBox");
    const point = entityElement(container, "evidence-point") as Element;

    fireEvent.pointerDown(point, { pointerId: 2, button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(canvas(), { pointerId: 2, clientX: 90, clientY: 90 });

    expect(store.getState().selection).toEqual(["evidence-point"]);
    expect(canvas().getAttribute("viewBox")).toBe(before);
    expect(entityElement(container, "evidence-point")?.getAttribute("color")).toBe(SELECTION_COLOR);
    expect(entityElement(container, "wall")?.getAttribute("color")).toBeNull();
  });

  it("takes its size from a ResizeObserver when no fixed size is given", () => {
    let notify: ResizeObserverCallback = () => undefined;
    class ResizeObserverStub {
      constructor(callback: ResizeObserverCallback) {
        notify = callback;
      }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    render(<EditorCanvas catalog={CATALOG} store={createEditorStore()} />);
    act(() => {
      notify(
        [{ contentRect: { width: 640, height: 480 } } as ResizeObserverEntry],
        {} as ResizeObserver,
      );
    });
    expect(canvas().getAttribute("width")).toBe("640");
    expect(canvas().getAttribute("height")).toBe("480");
  });
});

describe("catalog symbol markup", () => {
  it("renders the allow-listed shapes of a symbol with the layer color", () => {
    const { container } = renderCanvas();
    const marker = entityElement(container, "marker") as Element;
    expect(marker.getAttribute("data-symbol-id")).toBe("evidence_marker");
    expect(marker.querySelector("g")?.getAttribute("stroke")).toBe("currentColor");
    expect(marker.querySelector("g")?.getAttribute("stroke-width")).toBeNull();
    const circle = marker.querySelector("circle");
    expect(circle?.getAttribute("r")).toBe("40");
    expect(circle?.getAttribute("vector-effect")).toBe("non-scaling-stroke");
    expect(marker.getAttribute("transform")).toMatch(/^translate\(4 -4\) rotate\(0\) scale\(/);
  });

  it("keeps paper_mm symbols at a constant screen size and zooms world_m symbols", () => {
    const { container, zoom } = renderCanvas();
    /** Screen px per catalog SVG unit (100 per block unit) of an entity's symbol. */
    const screenUnit = (id: string): number => {
      const transform = entityElement(container, id)?.getAttribute("transform") ?? "";
      const match = /scale\(([^)]+)\)/.exec(transform);
      return Number(match?.[1]) * zoom();
    };
    const zoomBefore = zoom();
    // 6 mm marker: 6 CSS mm on screen. 0.9 m door: 0.9 m at the zoom; rotation is kept.
    expect(screenUnit("marker") * 100).toBeCloseTo(6 * CSS_PX_PER_MM, 6);
    expect(screenUnit("door") * 100).toBeCloseTo(0.9 * zoomBefore, 6);
    expect(entityElement(container, "door")?.getAttribute("transform")).toMatch(
      /^translate\(6 -2\) rotate\(-90\) scale\(0\.009/,
    );
    const marker = entityElement(container, "marker");
    const doorBefore = screenUnit("door");

    // A wheel step of factor exp(-deltaY * WHEEL_ZOOM_PER_PX) = 2.
    const deltaY = -Math.log(2) / WHEEL_ZOOM_PER_PX;
    fireEvent.wheel(canvas(), { deltaY, clientX: 100, clientY: 100 });

    expect(zoom() / zoomBefore).toBeCloseTo(2, 6);
    expect(screenUnit("marker") * 100).toBeCloseTo(6 * CSS_PX_PER_MM, 6);
    expect(screenUnit("door") / doorBefore).toBeCloseTo(2, 6);
    // The element is updated in place, not re-created.
    expect(entityElement(container, "marker")).toBe(marker);
  });

  it.each([
    "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    "<svg xmlns='http://www.w3.org/2000/svg'><circle r='1' onclick='alert(1)'/></svg>",
    "<svg xmlns='http://www.w3.org/2000/svg'><image href='http://evil.test/x.png'/></svg>",
    "<svg xmlns='http://www.w3.org/2000/svg'><rect style='fill:url(#a)'/></svg>",
    "<svg xmlns='http://www.w3.org/2000/svg'><g>text</g></svg>",
    "<svg xmlns='http://www.w3.org/2000/svg'><g>",
    "<div xmlns='http://www.w3.org/1999/xhtml'/>",
  ])("rejects markup outside the allow list: %s", (markup) => {
    expect(parseSymbolSvg(markup)).toBeNull();
  });
});
