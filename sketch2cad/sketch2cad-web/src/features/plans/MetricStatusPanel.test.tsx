import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { createEditorStore } from "../../editor/store/entities";
import type { EntityOut } from "../../editor/store/types";
import { MetricStatusPanel, type MetricStatusOut } from "./MetricStatusPanel";
import { PLAN_ID, REVISION_ID, fetchMock, jsonResponse, sent, stubFetch } from "./testing";

const STATUS_URL = `/api/v1/plans/${PLAN_ID}/metric-status`;
const IDS = [
  "00000000-0000-4000-8000-000000000101",
  "00000000-0000-4000-8000-000000000102",
  "00000000-0000-4000-8000-000000000103",
] as const;

function entityOut(id: string, overrides: Partial<EntityOut> = {}): EntityOut {
  return {
    id,
    type: "line",
    layer: "L_STRUCTURE",
    geometry: { kind: "line", coords_m: [[0, 0], [1, 0]] },
    attributes: {},
    confidence: null,
    constraint: null,
    created_by: null,
    deleted_at: null,
    discrepancy: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "trace_only",
    model_version: null,
    review_status: "accepted",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "manual" },
    updated_at: "2026-10-25T00:00:00Z",
    ...overrides,
  };
}

function status(overrides: Partial<MetricStatusOut> = {}): MetricStatusOut {
  return {
    status: "approximate",
    blocking_entities: [],
    reasons: {},
    discrepant_dimensions: [],
    length_conflicts: [],
    ...overrides,
  };
}

function setup(body: MetricStatusOut, entities: EntityOut[] = []) {
  stubFetch({ [`GET ${STATUS_URL}`]: jsonResponse(body) });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, entities);
  const api = createApiClient({ fetch: fetchMock });
  render(<MetricStatusPanel planId={PLAN_ID} api={api} store={store} />);
  return { store };
}

async function openPanel(text: string): Promise<HTMLElement> {
  const badge = await screen.findByRole("button", { name: text });
  expect(badge.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(badge);
  expect(badge.getAttribute("aria-expanded")).toBe("true");
  return screen.getByRole("region", { name: "Estado métrico del plano" });
}

describe("MetricStatusPanel", () => {
  afterEach(() => {
    cleanup();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("lists three blocking entities and clicking the second selects it", async () => {
    const { store } = setup(
      status({
        blocking_entities: [...IDS],
        reasons: { [IDS[0]]: "trace_only", [IDS[1]]: "none", [IDS[2]]: "trace_only" },
      }),
      [
        entityOut(IDS[0]),
        entityOut(IDS[1], {
          type: "point",
          layer: "L_EVIDENCE",
          geometry: { kind: "point", coords_m: [[1, 2]] },
          metric_origin: "none",
        }),
        entityOut(IDS[2], { type: "polyline", layer: "L_ACCESS", geometry: { kind: "polyline", coords_m: [[0, 0], [1, 1]], closed: false } }),
      ],
    );

    const panel = await openPanel("Estado métrico: Aproximado");
    const list = within(panel).getByRole("list", { name: "Entidades que impiden el estado métrico" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[1]?.textContent).toBe(
      "Punto · L_EVIDENCE · Sin origen métrico: colocada a mano sin cota",
    );
    expect(items[0]?.textContent).toContain("Línea · L_STRUCTURE · Solo trazo");

    fireEvent.click(within(items[1] as HTMLElement).getByRole("button"));
    expect(store.getState().selection).toEqual([IDS[1]]);
  });

  it("does nothing on click when the blocking entity is not in the store", async () => {
    const { store } = setup(status({ blocking_entities: [IDS[0]], reasons: { [IDS[0]]: "none" } }));

    const panel = await openPanel("Estado métrico: Aproximado");
    const item = within(panel).getByRole("button", { name: /Entidad no cargada/ });
    fireEvent.click(item);
    expect(store.getState().selection).toEqual([]);
  });

  it.each([
    ["metric", "Estado métrico: Métrico", "✓"],
    ["approximate", "Estado métrico: Aproximado", "!"],
    ["non_metric", "Estado métrico: No métrico", "✕"],
  ] as const)("shows the %s badge with text and icon", async (value, text, icon) => {
    setup(status({ status: value }));
    const badge = await screen.findByRole("button", { name: text });
    expect(badge.querySelector("[aria-hidden='true']")?.textContent).toBe(icon);
  });

  it("explains non_metric as uncalibrated without a list", async () => {
    setup(status({ status: "non_metric" }));
    const panel = await openPanel("Estado métrico: No métrico");
    expect(panel.textContent).toContain("sin calibración");
    expect(within(panel).queryByRole("list")).toBeNull();
  });

  it("explains a failed constraint of a blocking entity", async () => {
    setup(status({ blocking_entities: [IDS[0]], reasons: { [IDS[0]]: "none" } }), [
      entityOut(IDS[0], {
        type: "point",
        layer: "L_EVIDENCE",
        geometry: { kind: "point", coords_m: [[1, 2]] },
        metric_origin: "none",
        attributes: { constraint_error: "V-06" },
      }),
    ]);
    const panel = await openPanel("Estado métrico: Aproximado");
    expect(within(panel).getByText(/Las distancias ya no se cruzan/)).toBeTruthy();
  });

  it("lists the discrepant dimensions as information", async () => {
    const { store } = setup(status({ status: "metric", discrepant_dimensions: [IDS[0]] }), [
      entityOut(IDS[0], {
        type: "dimension",
        layer: "L_DIMENSIONS",
        geometry: {
          kind: "dimension",
          p1_m: [0, 0],
          p2_m: [1, 0],
          offset_m: 0.5,
          declared_value_m: 1.2,
          measured_value_m: 1,
        },
        metric_origin: "declared_dimension",
        discrepancy: true,
      }),
    ]);
    const panel = await openPanel("Estado métrico: Métrico");
    const list = within(panel).getByRole("list", { name: "Cotas con discrepancia (informativo)" });
    fireEvent.click(within(list).getByRole("button"));
    expect(store.getState().selection).toEqual([IDS[0]]);
  });

  it("lists the entities whose declared lengths a connected fix broke", async () => {
    setup(status({ status: "approximate", length_conflicts: [IDS[0]] }), [entityOut(IDS[0])]);
    const panel = await openPanel("Estado métrico: Aproximado");
    const list = within(panel).getByRole("list", {
      name: "Longitudes declaradas que dejaron de cumplirse (revisar)",
    });
    expect(within(list).getByRole("button").textContent).toContain("vuelve a fijarla");
  });

  it("fetches again when the queue drains and when the store is reloaded", async () => {
    const { store } = setup(status());
    await screen.findByRole("button", { name: "Estado métrico: Aproximado" });
    expect(sent("GET", STATUS_URL)).toHaveLength(1);

    act(() => {
      store.getState().addEntity({
        type: "line",
        layer: "L_STRUCTURE",
        geometry: { kind: "line", coords_m: [[0, 0], [1, 0]] },
      });
    });
    expect(sent("GET", STATUS_URL)).toHaveLength(1);

    stubFetch({ [`GET ${STATUS_URL}`]: jsonResponse(status({ status: "metric" })) });
    act(() => {
      const head = store.getState().beginSync();
      store.getState().ackSync(head?.seq ?? -1, "server-1");
    });
    await screen.findByRole("button", { name: "Estado métrico: Métrico" });
    expect(sent("GET", STATUS_URL)).toHaveLength(2);

    stubFetch({ [`GET ${STATUS_URL}`]: jsonResponse(status({ status: "non_metric" })) });
    act(() => {
      store.getState().load(REVISION_ID, []);
    });
    await screen.findByRole("button", { name: "Estado métrico: No métrico" });
    await waitFor(() => expect(sent("GET", STATUS_URL)).toHaveLength(3));
  });
});
