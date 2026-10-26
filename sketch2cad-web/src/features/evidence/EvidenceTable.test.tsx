import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { createPlanStore } from "../../editor/plan/planStore";
import { createEditorStore } from "../../editor/store/entities";
import type { EntityOut } from "../../editor/store/types";
import {
  CASE_ID,
  REVISION_ID,
  fetchMock,
  jsonResponse,
  planOut,
  sent,
  stubFetch,
  type Routes,
} from "../plans/testing";
import { EvidenceTable, formatMetres, type EvidenceOut } from "./EvidenceTable";

const EVIDENCE_URL = `/api/v1/case-files/${CASE_ID}/evidence`;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const EV1 = "00000000-0000-4000-8000-000000000e01";
const EV2 = "00000000-0000-4000-8000-000000000e02";
const POINT_ID = "00000000-0000-4000-8000-000000000201";
const SYMBOL_ID = "00000000-0000-4000-8000-000000000202";

function evidenceOut(id: string, number: number, label: string): EvidenceOut {
  return {
    id,
    number,
    label,
    attributes: {},
    case_file_id: CASE_ID,
    created_at: "2026-10-25T00:00:00Z",
    created_by: "00000000-0000-4000-8000-0000000000e1",
  };
}

function entityOut(id: string, overrides: Partial<EntityOut> = {}): EntityOut {
  return {
    id,
    type: "point",
    layer: "L_EVIDENCE",
    geometry: { kind: "point", coords_m: [[12.5, 3.14159]] },
    attributes: {},
    confidence: null,
    constraint: null,
    created_by: null,
    deleted_at: null,
    discrepancy: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "triangulation",
    model_version: null,
    review_status: "accepted",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "manual" },
    updated_at: "2026-10-25T00:00:00Z",
    ...overrides,
  };
}

const PLACED = entityOut(POINT_ID, { evidence_id: EV1 });
const SYMBOL = entityOut(SYMBOL_ID, {
  type: "symbol",
  geometry: { kind: "symbol", anchor_m: [-0.5, 2], rotation_deg: 0, scale: 1, symbol_id: "evidence_marker" },
  metric_origin: "baseline_offset",
});

function setup(entities: EntityOut[], routes: Routes = {}) {
  stubFetch({
    [`GET ${EVIDENCE_URL}`]: jsonResponse({
      items: [evidenceOut(EV2, 2, "Llave"), evidenceOut(EV1, 1, "Casquillo")],
    }),
    ...routes,
  });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, entities);
  const plan = createPlanStore();
  plan.getState().setPlan(planOut());
  const api = createApiClient({ fetch: fetchMock });
  render(<EvidenceTable caseId={CASE_ID} api={api} store={store} plan={plan} />);
  return { store };
}

async function rows(): Promise<HTMLElement[]> {
  const table = await screen.findByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

function cells(row: HTMLElement | undefined): string[] {
  return [...(row as HTMLElement).querySelectorAll("th, td")]
    .slice(0, 5)
    .map((cell) => cell.textContent ?? "");
}

describe("formatMetres", () => {
  it.each([
    [12.5, "12,50"],
    [3.14159, "3,14"],
    [-0.5, "-0,50"],
    [0, "0,00"],
    [-0.001, "0,00"],
  ])("formats %d as %s", (value, text) => {
    expect(formatMetres(value)).toBe(text);
  });
});

describe("EvidenceTable", () => {
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("joins the register with the placing entities and lists unplaced evidence", async () => {
    setup([PLACED, SYMBOL]);
    const [first, second] = await rows();
    expect(cells(first)).toEqual(["1", "Casquillo", "12,50", "3,14", "Triangulación"]);
    expect(cells(second)).toEqual(["2", "Llave", "sin ubicar", "sin ubicar", "sin ubicar"]);
  });

  it("selects the placing entity when its row is clicked", async () => {
    const { store } = setup([PLACED, SYMBOL]);
    const [first, second] = await rows();
    fireEvent.click(second as HTMLElement);
    expect(store.getState().selection).toEqual([]);
    fireEvent.click(within(first as HTMLElement).getByText("Casquillo"));
    expect(store.getState().selection).toEqual([POINT_ID]);
  });

  it("creates evidence with the label and reloads the register", async () => {
    setup([], { [`POST ${EVIDENCE_URL}`]: jsonResponse(evidenceOut(EV1, 3, "Vaso"), 201) });
    await rows();
    const form = screen.getByRole("form", { name: "Agregar indicio" });

    fireEvent.submit(form);
    expect(screen.getByRole("alert").textContent).toBe("Escribe una etiqueta.");
    expect(sent("POST", EVIDENCE_URL)).toEqual([]);

    fireEvent.change(within(form).getByLabelText("Etiqueta del nuevo indicio"), {
      target: { value: "  Vaso " },
    });
    fireEvent.submit(form);
    await waitFor(() => expect(sent("POST", EVIDENCE_URL)).toEqual([{ label: "Vaso" }]));
    await waitFor(() => expect(sent("GET", EVIDENCE_URL)).toHaveLength(2));
  });

  it("edits a label inline through PATCH /evidence/{id}", async () => {
    setup([PLACED], {
      [`PATCH /api/v1/evidence/${EV1}`]: jsonResponse(evidenceOut(EV1, 1, "Casquillo 9 mm")),
    });
    const [first] = await rows();
    fireEvent.click(within(first as HTMLElement).getByRole("button", { name: "Editar etiqueta" }));
    fireEvent.change(screen.getByLabelText("Etiqueta del indicio 1"), {
      target: { value: "Casquillo 9 mm" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(sent("PATCH", `/api/v1/evidence/${EV1}`)).toEqual([{ label: "Casquillo 9 mm" }]),
    );
  });

  it("assigns evidence to the selected symbol with a relabel through serverOps", async () => {
    const assigned = { ...SYMBOL, evidence_id: EV2 };
    const { store } = setup([PLACED, SYMBOL], {
      [`PATCH /api/v1/entities/${SYMBOL_ID}`]: jsonResponse({ ...assigned, candidates: null }),
      [`GET ${ENTITIES_URL}`]: jsonResponse({ entities: [PLACED, assigned] }),
    });
    const [, second] = await rows();
    const assign = within(second as HTMLElement).getByRole("button", {
      name: "Asignar al elemento seleccionado: 2",
    }) as HTMLButtonElement;
    expect(assign.disabled).toBe(true);

    act(() => {
      store.getState().select([SYMBOL_ID]);
    });
    expect(assign.disabled).toBe(false);
    fireEvent.click(assign);

    await waitFor(() =>
      expect(sent("PATCH", `/api/v1/entities/${SYMBOL_ID}`)).toEqual([
        { operation: "relabel", evidence_id: EV2 },
      ]),
    );
    await waitFor(async () => {
      const [, row] = await rows();
      expect(cells(row)).toEqual(["2", "Llave", "-0,50", "2,00", "Base y offset"]);
    });
  });
});
