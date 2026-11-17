import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { planOut } from "../../features/plans/testing";
import { EditorWorkspace } from "../EditorWorkspace";
import type { Catalog } from "../canvas/EditorCanvas";
import { worldToScreen, type Viewport } from "../canvas/viewport";
import { createSnapSettingsStore } from "../panels/SnapSettings";
import {
  createPlanStore,
  isMetricCalibration,
  type CalibrationOut,
  type PlanStore,
} from "../plan/planStore";
import { createEditorStore } from "../store/entities";
import type { EntityOut, Geometry, Point2 } from "../store/types";
import { createEditorUiStore, type ToolId } from "../ui/editorUi";
import type { StoreApi } from "zustand/vanilla";
import { MEASURE_TEXT, createMeasureTool, measureDistance } from "./MeasureTool";

const REVISION_ID = "00000000-0000-4000-8000-0000000000r1";
/** Client position of the canvas's top-left corner (stubbed layout). */
const ORIGIN = { left: 40, top: 30 };
/** Any metric-looking distance: digits, a decimal comma, two decimals and "m". */
const METRES = /\d+,\d{2} m/;

const CATALOG: Catalog = {
  id: "00000000-0000-4000-8000-0000000000c1",
  version: "default-1",
  layers: [
    { name: "L_STRUCTURE", description: "Muros", aci_color: 7 },
    { name: "L_SHEET", description: "Cajetín", aci_color: 7 },
  ],
  symbols: [],
};

function calibration(overrides: Partial<CalibrationOut> = {}): CalibrationOut {
  return {
    id: "00000000-0000-4000-8000-0000000000k1",
    accepted_at: "2026-10-25T00:00:00Z",
    accepted_by: "00000000-0000-4000-8000-0000000000e1",
    as_metric: true,
    control_points: [],
    image_to_paper: null,
    local_frame: null,
    method: "scan_dpi",
    paper_scale: null,
    reprojection_error_mm: null,
    ...overrides,
  };
}

/** A plan store filled from `PlanOut` as EditorPage does, with `calibration`. */
function planWith(value: CalibrationOut | null): StoreApi<PlanStore> {
  const plan = createPlanStore();
  plan.getState().setPlan(planOut({ calibration: value }));
  return plan;
}

function entityOut(id: string, geometry: Geometry): EntityOut {
  return {
    id,
    type: geometry.kind,
    layer: "L_STRUCTURE",
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

/** A wall from (0, 0) to (10, 0) and a mark at (3, 4): the canvas fits around them. */
const ENTITIES: EntityOut[] = [
  entityOut("wall", { kind: "line", coords_m: [[0, 0], [10, 0]] }),
  entityOut("mark", { kind: "point", coords_m: [[3, 4]] }),
];

interface SetupOptions {
  tool?: ToolId;
  plan?: StoreApi<PlanStore>;
  entities?: EntityOut[];
  gridM?: number;
}

function setup({
  tool = "select",
  plan = planWith(calibration()),
  entities = ENTITIES,
  gridM,
}: SetupOptions = {}) {
  const store = createEditorStore();
  store.getState().load(REVISION_ID, entities);
  const ui = createEditorUiStore({ activeTool: tool });
  const snap = createSnapSettingsStore(null);
  if (gridM !== undefined) snap.getState().setGridM(gridM);
  let viewport: Viewport | null = null;
  render(
    <EditorWorkspace
      catalog={CATALOG}
      store={store}
      ui={ui}
      plan={plan}
      snap={snap}
      width={800}
      height={600}
      onViewportChange={(next) => {
        viewport = next;
      }}
    />,
  );
  const svg = screen.getByTestId("editor-canvas") as unknown as SVGSVGElement;
  svg.getBoundingClientRect = () => new DOMRect(ORIGIN.left, ORIGIN.top, 800, 600);
  /** Client position of a world point, `dx`/`dy` screen px away, with the current camera. */
  const client = (point: Point2, dx = 0, dy = 0) => {
    if (viewport === null) throw new Error("No viewport yet");
    const [x, y] = worldToScreen(viewport, point);
    return { clientX: ORIGIN.left + x + dx, clientY: ORIGIN.top + y + dy };
  };
  /** Document state a measurement must never touch. */
  const documentState = () => {
    const state = store.getState();
    return { entities: state.entities, queue: state.queue.ops.length, past: state.history.past.length };
  };
  return { store, ui, svg, client, documentState };
}

type Client = { clientX: number; clientY: number };
function press(svg: Element, at: Client) {
  fireEvent.pointerDown(svg, { pointerId: 1, button: 0, ...at });
}
function move(svg: Element, at: Client) {
  fireEvent.pointerMove(svg, { pointerId: 1, ...at });
}
function release(svg: Element, at: Client) {
  fireEvent.pointerUp(svg, { pointerId: 1, button: 0, ...at });
}
function click(svg: Element, at: Client) {
  press(svg, at);
  release(svg, at);
}
function key(name: string, target: Element = document.body, init: Record<string, unknown> = {}) {
  fireEvent.keyDown(target, { key: name, ...init });
}
function status(): string {
  return screen.getByTestId("measure-status").querySelector("[role='status']")?.textContent ?? "";
}

afterEach(cleanup);

describe("measureDistance", () => {
  it("is the Euclidean distance in metres", () => {
    expect(measureDistance([1, 1], [4, 5])).toBe(5);
    expect(measureDistance([4, 5], [1, 1])).toBe(5);
    expect(measureDistance([2, 2], [2, 2])).toBe(0);
  });
});

describe("isMetricCalibration", () => {
  it("is true only for a calibration accepted as metric with a real method", () => {
    expect(isMetricCalibration(null)).toBe(false);
    expect(isMetricCalibration(undefined)).toBe(false);
    expect(isMetricCalibration(calibration())).toBe(true);
    expect(isMetricCalibration(calibration({ as_metric: false }))).toBe(false);
    expect(isMetricCalibration(calibration({ method: "none", as_metric: true }))).toBe(false);
  });

  it("is taken from the PlanOut by setPlan", () => {
    expect(planWith(calibration()).getState().metricCalibration).toBe(true);
    expect(planWith(null).getState().metricCalibration).toBe(false);
    expect(planWith(calibration({ as_metric: false })).getState().metricCalibration).toBe(false);
  });
});

describe("measure tool", () => {
  it("is in the palette and M selects it", () => {
    const { ui } = setup();
    const button = screen.getByRole("button", { name: "Medir" });
    expect(button.getAttribute("aria-keyshortcuts")).toBe("M");
    expect(button.getAttribute("aria-pressed")).toBe("false");

    act(() => key("m"));
    expect(ui.getState().activeTool).toBe("measure");
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(status()).toBe(MEASURE_TEXT.pickFirst);

    act(() => key("V"));
    act(() => key("M"));
    expect(ui.getState().activeTool).toBe("measure");
  });

  it("shows 5,00 m for two points 3 m apart horizontally and 4 m vertically", () => {
    const { svg, client } = setup();
    act(() => key("M"));

    act(() => click(svg, client([1, 1])));
    expect(status()).toBe(MEASURE_TEXT.pickSecond);
    act(() => click(svg, client([4, 5])));

    expect(status()).toBe("Distancia: 5,00 m");
    expect(screen.getByTestId("measure-label").textContent).toBe("5,00 m");
    expect(screen.getByTestId("measure-segment")).toBeTruthy();
  });

  it("creates no entity, history step or API operation", () => {
    const { svg, client, documentState } = setup();
    const before = documentState();
    act(() => key("M"));
    act(() => click(svg, client([1, 1])));
    act(() => click(svg, client([4, 5])));
    act(() => key("Escape"));
    act(() => click(svg, client([1, 2])));
    act(() => click(svg, client([7, 2])));

    expect(status()).toBe("Distancia: 6,00 m");
    const after = documentState();
    expect(after.entities).toBe(before.entities);
    expect(after).toEqual({ ...before, queue: 0, past: 0 });
  });

  it("snaps to entity endpoints", () => {
    const { svg, client } = setup();
    act(() => key("M"));
    // 5 px off each vertex, outwards: unsnapped, the distance would read more than 5,05 m.
    act(() => click(svg, client([0, 0], -5, 5)));
    act(() => click(svg, client([3, 4], 5, -5)));
    expect(status()).toBe("Distancia: 5,00 m");
  });

  it("snaps the second point orthogonally to the first", () => {
    const { svg, client } = setup();
    act(() => key("M"));
    act(() => click(svg, client([1, 2])));
    // 0.38° above the horizontal: within the orthogonal tolerance.
    act(() => click(svg, client([7, 2.04])));
    const points = screen.getByTestId("measure-segment").querySelector("polyline");
    const [first, second] = (points?.getAttribute("points") ?? "").split(" ");
    expect(second?.split(",")[1]).toBe(first?.split(",")[1]);
    expect(status()).toBe("Distancia: 6,00 m");
  });

  it("follows the pointer with the live distance before the second point", () => {
    const { svg, client } = setup();
    act(() => key("M"));
    expect(screen.queryByTestId("measure-label")).toBeNull();
    act(() => click(svg, client([1, 1])));
    act(() => move(svg, client([4, 5])));
    expect(screen.getByTestId("measure-label").textContent).toBe("5,00 m");
    // The status is announced on clicks only, not on every pointer move.
    expect(status()).toBe(MEASURE_TEXT.pickSecond);
  });

  it("measures with a press-drag-release", () => {
    const { svg, client } = setup();
    act(() => key("M"));
    act(() => press(svg, client([1, 1])));
    act(() => move(svg, client([4, 5])));
    act(() => release(svg, client([4, 5])));
    expect(status()).toBe("Distancia: 5,00 m");
  });

  it("keeps one measurement at a time: the next click starts a new one", () => {
    const { svg, client } = setup();
    act(() => key("M"));
    act(() => click(svg, client([1, 1])));
    act(() => click(svg, client([4, 5])));
    expect(status()).toBe("Distancia: 5,00 m");

    act(() => click(svg, client([1, 2])));
    expect(screen.queryByTestId("measure-segment")).toBeNull();
    expect(status()).toBe(MEASURE_TEXT.pickSecond);
    act(() => click(svg, client([7, 2])));
    expect(screen.getAllByTestId("measure-segment")).toHaveLength(1);
    expect(screen.getAllByTestId("measure-label")).toHaveLength(1);
    expect(status()).toBe("Distancia: 6,00 m");
  });

  it("ignores a second click on the first point", () => {
    const { svg, client } = setup();
    act(() => key("M"));
    act(() => click(svg, client([1, 1])));
    act(() => click(svg, client([1, 1], 1, 0)));
    expect(status()).toBe(MEASURE_TEXT.pickSecond);
    expect(screen.queryByTestId("measure-segment")).toBeNull();
  });

  it("clears with Esc and when another tool is chosen", () => {
    const { svg, client, ui } = setup();
    act(() => key("M"));
    act(() => click(svg, client([1, 1])));
    act(() => key("Escape"));
    expect(ui.getState().draft).toEqual([]);
    expect(status()).toBe(MEASURE_TEXT.pickFirst);

    act(() => click(svg, client([1, 1])));
    act(() => click(svg, client([4, 5])));
    act(() => key("Escape"));
    expect(screen.queryByTestId("measure-segment")).toBeNull();
    expect(status()).toBe(MEASURE_TEXT.pickFirst);

    act(() => click(svg, client([1, 1])));
    act(() => click(svg, client([4, 5])));
    act(() => key("L"));
    act(() => key("M"));
    expect(screen.queryByTestId("measure-segment")).toBeNull();
    expect(status()).toBe(MEASURE_TEXT.pickFirst);
  });

  it("measures with the keyboard cursor: Enter, arrows, Enter", () => {
    // No entities: nothing to snap to, so the cursor's grid steps are the measured legs.
    const { svg } = setup({ entities: [], gridM: 0.5 });
    svg.focus();
    act(() => key("m", svg));
    act(() => key("Enter", svg));
    for (let i = 0; i < 6; i += 1) act(() => key("ArrowRight", svg));
    for (let i = 0; i < 8; i += 1) act(() => key("ArrowUp", svg));
    act(() => key("Enter", svg));
    expect(status()).toBe("Distancia: 5,00 m");
  });
});

describe("measure tool without a metric calibration", () => {
  const cases: [string, StoreApi<PlanStore>][] = [
    ["an uncalibrated plan", planWith(null)],
    ["a calibration not accepted as metric", planWith(calibration({ as_metric: false }))],
    ["a calibration with method none", planWith(calibration({ method: "none", as_metric: false }))],
  ];

  it.each(cases)("shows no metric distance for %s", (_name, plan) => {
    const { svg, client, ui, documentState } = setup({ plan });
    const before = documentState();
    act(() => key("M"));
    expect(ui.getState().activeTool).toBe("measure");
    expect(status()).toBe(MEASURE_TEXT.notMetric);

    act(() => click(svg, client([1, 1])));
    act(() => move(svg, client([4, 5])));
    act(() => click(svg, client([4, 5])));

    expect(status()).toBe(MEASURE_TEXT.notMetric);
    expect(ui.getState().draft).toEqual([]);
    expect(screen.queryByTestId("measure-label")).toBeNull();
    expect(screen.queryByTestId("measure-segment")).toBeNull();
    expect(document.body.textContent).not.toMatch(METRES);
    expect(documentState()).toEqual(before);
  });

  it("without a plan store the tool stays non-metric", () => {
    // ToolContext.plan is optional: a tool built without one must not assume calibration.
    const store = createEditorStore();
    const ui = createEditorUiStore({ activeTool: "measure" });
    const tool = createMeasureTool({ store, ui, catalog: CATALOG });
    tool.onPointerDown?.({ world: [1, 1], scale: 10, entityId: null, shiftKey: false });
    expect(ui.getState().draft).toEqual([]);
  });
});
