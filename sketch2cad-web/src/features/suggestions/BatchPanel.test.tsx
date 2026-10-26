import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes as RouterRoutes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { EditorCanvas, type Catalog } from "../../editor/canvas/EditorCanvas";
import catalogJson from "../../editor/fixtures/catalog-default-1.json";
import { createPlanStore } from "../../editor/plan/planStore";
import { createEditorStore } from "../../editor/store/entities";
import type { EntityOut } from "../../editor/store/types";
import { authStore } from "../auth/authStore";
import { planEditorPath } from "../calibration/CalibrationPage";
import { EditorPage } from "../plans/EditorPage";
import {
  CASE_ID,
  PLAN_ID,
  REVISION_ID,
  SOURCE_ID,
  USER_ID,
  fetchMock,
  jsonResponse,
  planOut,
  sent,
  signInAs,
  stubFetch,
  type Routes,
} from "../plans/testing";
import { BATCH_TEXT, BULK_REVIEW_MAX_IDS, BatchPanel } from "./BatchPanel";
import { HIGHLIGHT_PARAM } from "./aiAssist";
import { LOW_CONFIDENCE_CLASS, SuggestionLayer } from "./SuggestionLayer";

const CATALOG = catalogJson as Catalog;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const BULK_URL = `/api/v1/revisions/${REVISION_ID}/entities:bulk-review`;
const OWN_LEASE = { user_id: USER_ID, display_name: "technician", expires_at: "2999-01-01T00:00:00Z" };
const S1 = "00000000-0000-4000-8000-000000005001";
const S2 = "00000000-0000-4000-8000-000000005002";
const S3 = "00000000-0000-4000-8000-000000005003";
const MANUAL = "00000000-0000-4000-8000-000000005009";

function entity(id: string, overrides: Partial<EntityOut> = {}): EntityOut {
  return {
    id,
    type: "polyline",
    layer: "L_STRUCTURE",
    geometry: { kind: "polyline", coords_m: [[0, 0], [2, 0], [2, 1]], closed: false },
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
    updated_at: "2026-10-26T00:00:00Z",
    ...overrides,
  };
}

function suggestion(id: string, confidence: number): EntityOut {
  return entity(id, {
    confidence,
    review_status: "suggested",
    source: { method: "cv" },
    model_version: "vision-1",
  });
}

const SUGGESTIONS = [suggestion(S1, 0.9), suggestion(S2, 0.5), suggestion(S3, 0.75)];
const ENTITIES = [...SUGGESTIONS, entity(MANUAL)];

function configRoutes(enabled: boolean, threshold?: number): Routes {
  const params =
    threshold === undefined
      ? []
      : [
          {
            name: HIGHLIGHT_PARAM,
            default: 0.7,
            overrides: [
              {
                scope: "global",
                incident_type: null,
                value: threshold,
                updated_at: "2026-10-26T00:00:00Z",
                updated_by: USER_ID,
              },
            ],
            constraints: {},
            description: "",
            env_var: "SUGGESTIONS_MIN_CONFIDENCE_HIGHLIGHT",
            scopes: ["global", "incident_type"],
            section: "suggestions",
            type: "float",
          },
        ];
  return {
    "GET /api/v1/config/features": jsonResponse({ ai_assist: { enabled } }),
    "GET /api/v1/config/params": jsonResponse({ params }),
  };
}

function bulkResponse(ids: string[], status: "accepted" | "rejected") {
  return jsonResponse({
    review_status: status,
    changed: ids.length,
    unchanged: 0,
    changed_ids: ids,
    unchanged_ids: [],
    metric_status: "approximate",
  });
}

interface SetupOptions {
  entities?: EntityOut[];
  routes?: Routes;
  enabled?: boolean;
  threshold?: number;
  lease?: boolean;
}

/** Batch panel next to a canvas with the suggestion layer, as the editor page mounts them. */
function setup({ entities = ENTITIES, routes = {}, enabled = true, threshold, lease = true }: SetupOptions = {}) {
  stubFetch({ ...configRoutes(enabled, threshold), ...routes });
  const api = createApiClient({ fetch: fetchMock });
  const store = createEditorStore();
  store.getState().load(REVISION_ID, entities);
  const plan = createPlanStore({
    planId: PLAN_ID,
    revisionId: REVISION_ID,
    userId: USER_ID,
    lease: lease ? OWN_LEASE : null,
  });
  render(
    <>
      <BatchPanel api={api} store={store} plan={plan} />
      <EditorCanvas
        catalog={CATALOG}
        store={store}
        width={400}
        height={300}
        suggestionLayer={<SuggestionLayer store={store} api={api} />}
      />
    </>,
  );
  return { store, plan };
}

async function openPanel(count = SUGGESTIONS.length): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: BATCH_TEXT.toggle(count) }));
  return screen.getByRole("region", { name: BATCH_TEXT.title });
}

function suggestionElement(id: string): Element | null {
  return screen.getByTestId("editor-canvas").querySelector(`[data-suggestion][data-entity-id="${id}"]`);
}

describe("BatchPanel and SuggestionLayer", () => {
  afterEach(() => {
    cleanup();
    authStore.reset();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("accept-all sends one bulk request with every pending id and reloads the entities", async () => {
    const accepted = SUGGESTIONS.map((s) => ({ ...s, review_status: "accepted" as const }));
    const { store } = setup({
      routes: {
        [`POST ${BULK_URL}`]: bulkResponse([S2, S3, S1], "accepted"),
        [`GET ${ENTITIES_URL}`]: jsonResponse({ entities: [...accepted, entity(MANUAL)] }),
      },
    });
    const panel = await openPanel();

    fireEvent.click(within(panel).getByRole("button", { name: BATCH_TEXT.acceptAll }));

    expect(await within(panel).findByText("Aceptadas: 3 sugerencias.")).toBeTruthy();
    const requests = sent("POST", BULK_URL);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toEqual({ ids: [S2, S3, S1], review_status: "accepted" });
    // Accepted suggestions are normal entities now: drawn with their layer, not as suggestions.
    expect(store.getState().entities.byId[S1]?.remote?.review_status).toBe("accepted");
    expect(suggestionElement(S1)).toBeNull();
    expect(document.querySelector(`[data-layer="L_STRUCTURE"] [data-entity-id="${S1}"]`)).not.toBeNull();
    expect(screen.getByRole("button", { name: BATCH_TEXT.toggle(0) })).toBeTruthy();
  });

  it("gives the low-confidence class to a suggestion with confidence 0.5", async () => {
    setup();
    await waitFor(() => expect(suggestionElement(S2)).not.toBeNull());
    expect(suggestionElement(S2)?.classList.contains(LOW_CONFIDENCE_CLASS)).toBe(true);
    expect(suggestionElement(S1)?.classList.contains(LOW_CONFIDENCE_CLASS)).toBe(false);
    expect(suggestionElement(S3)?.classList.contains(LOW_CONFIDENCE_CLASS)).toBe(false);
    // Suggestions are drawn by the suggestion layer only, the manual entity by its layer.
    const layer = document.querySelector('[data-layer="L_STRUCTURE"]');
    expect(layer?.querySelector(`[data-entity-id="${S2}"]`)).toBeNull();
    expect(layer?.querySelector(`[data-entity-id="${MANUAL}"]`)).not.toBeNull();
  });

  it("reads the highlight threshold from the configuration", async () => {
    setup({ threshold: 0.8 });
    await waitFor(() =>
      expect(suggestionElement(S3)?.classList.contains(LOW_CONFIDENCE_CLASS)).toBe(true),
    );
    expect(suggestionElement(S1)?.classList.contains(LOW_CONFIDENCE_CLASS)).toBe(false);
    const panel = await openPanel();
    expect(within(panel).getByText(BATCH_TEXT.low(0.8))).toBeTruthy();
  });

  it("counts the pending suggestions by confidence band", async () => {
    setup();
    const panel = await openPanel();
    expect(within(panel).getByText(BATCH_TEXT.high(0.7))).toBeTruthy();
    expect(within(panel).getByTestId("band-high").textContent).toBe("2");
    expect(within(panel).getByTestId("band-low").textContent).toBe("1");
    const items = within(panel).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "Sugerencia 1 · confianza 0,50 · bajaAceptarRechazar",
      "Sugerencia 2 · confianza 0,75AceptarRechazar",
      "Sugerencia 3 · confianza 0,90AceptarRechazar",
    ]);
  });

  it("reject-all sends one rejected bulk request and confirms the count", async () => {
    setup({
      routes: {
        [`POST ${BULK_URL}`]: bulkResponse([S2, S3, S1], "rejected"),
        [`GET ${ENTITIES_URL}`]: jsonResponse({
          entities: [
            ...SUGGESTIONS.map((s) => ({
              ...s,
              review_status: "rejected" as const,
              deleted_at: "2026-10-26T00:00:01Z",
            })),
            entity(MANUAL),
          ],
        }),
      },
    });
    const panel = await openPanel();
    fireEvent.click(within(panel).getByRole("button", { name: BATCH_TEXT.rejectAll }));

    expect(await within(panel).findByText("Rechazadas: 3 sugerencias.")).toBeTruthy();
    expect(sent("POST", BULK_URL)).toEqual([{ ids: [S2, S3, S1], review_status: "rejected" }]);
    expect(within(panel).getByText(BATCH_TEXT.none)).toBeTruthy();
    expect(suggestionElement(S1)).toBeNull();
  });

  it("splits more than 2000 pending ids into requests of at most 2000", async () => {
    const many = Array.from({ length: BULK_REVIEW_MAX_IDS + 1 }, (_, i) =>
      suggestion(`00000000-0000-4000-8000-${String(i).padStart(12, "0")}`, 0.9),
    );
    setup({
      entities: many,
      routes: {
        [`POST ${BULK_URL}`]: (init) => {
          const body = JSON.parse(String(init?.body)) as { ids: string[] };
          return bulkResponse(body.ids, "accepted");
        },
        [`GET ${ENTITIES_URL}`]: jsonResponse({ entities: [] }),
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: BATCH_TEXT.toggle(many.length) }));
    fireEvent.click(screen.getByRole("button", { name: BATCH_TEXT.acceptAll }));

    expect(await screen.findByText("Aceptadas: 2001 sugerencias.")).toBeTruthy();
    const requests = sent("POST", BULK_URL) as { ids: string[] }[];
    expect(requests.map((r) => r.ids.length)).toEqual([2000, 1]);
  });

  it("selects an item on the canvas and reviews a single suggestion", async () => {
    const { store } = setup({
      routes: {
        [`POST ${BULK_URL}`]: bulkResponse([S2], "rejected"),
        [`GET ${ENTITIES_URL}`]: jsonResponse({
          entities: ENTITIES.map((e) =>
            e.id === S2 ? { ...e, review_status: "rejected" as const, deleted_at: "2026-10-26T00:00:01Z" } : e,
          ),
        }),
      },
    });
    const panel = await openPanel();
    const [first] = within(panel).getAllByRole("listitem");

    fireEvent.click(within(first as HTMLElement).getByRole("button", { name: /Sugerencia 1/ }));
    expect(store.getState().selection).toEqual([S2]);
    expect(suggestionElement(S2)?.getAttribute("color")).toBe("#ff7a00");

    fireEvent.click(within(panel).getByRole("button", { name: BATCH_TEXT.rejectItem(1) }));
    expect(await within(panel).findByText("Rechazadas: 1 sugerencia.")).toBeTruthy();
    expect(sent("POST", BULK_URL)).toEqual([{ ids: [S2], review_status: "rejected" }]);
    expect(within(panel).getAllByRole("listitem")).toHaveLength(2);
  });

  it("selects a suggestion clicked on the canvas", async () => {
    const { store } = setup();
    await waitFor(() => expect(suggestionElement(S3)).not.toBeNull());
    const shape = suggestionElement(S3)?.querySelector("polyline");
    fireEvent.pointerDown(shape as Element, { button: 0, pointerId: 1 });
    expect(store.getState().selection).toEqual([S3]);
  });

  it("shows the 422 bulk_review_invalid problem and changes nothing", async () => {
    const { store } = setup({
      routes: {
        [`POST ${BULK_URL}`]: jsonResponse(
          {
            title: "Unprocessable",
            status: 422,
            code: "bulk_review_invalid",
            invalid_ids: [S1],
            reasons: { [S1]: "deleted" },
          },
          422,
        ),
      },
    });
    const panel = await openPanel();
    fireEvent.click(within(panel).getByRole("button", { name: BATCH_TEXT.acceptAll }));

    expect((await within(panel).findByRole("alert")).textContent).toBe(BATCH_TEXT.invalid(1));
    expect(sent("GET", ENTITIES_URL)).toHaveLength(0);
    expect(store.getState().entities.byId[S1]?.remote?.review_status).toBe("suggested");
  });

  it.each([
    [
      409,
      { code: "lease_required" },
      "Ya no tienes el permiso de edición de este plano. Recarga la página para volver a editar.",
    ],
    [403, {}, "No tienes permiso para esta acción."],
  ])("shows the %d problem of a refused review", async (status, extra, message) => {
    setup({ routes: { [`POST ${BULK_URL}`]: jsonResponse({ title: "Refused", status, ...extra }, status) } });
    const panel = await openPanel();
    fireEvent.click(within(panel).getByRole("button", { name: BATCH_TEXT.acceptItem(1) }));
    expect((await within(panel).findByRole("alert")).textContent).toBe(message);
  });

  it("disables the review buttons with an explanation without the edit lease", async () => {
    setup({ lease: false });
    const panel = await openPanel();
    const acceptAll = within(panel).getByRole("button", { name: BATCH_TEXT.acceptAll });
    expect((acceptAll as HTMLButtonElement).disabled).toBe(true);
    const hint = within(panel).getByText(BATCH_TEXT.needsLease);
    expect(acceptAll.getAttribute("aria-describedby")).toBe(hint.id);
    expect(
      (within(panel).getByRole("button", { name: BATCH_TEXT.rejectItem(1) }) as HTMLButtonElement).disabled,
    ).toBe(true);
    // Selecting still works: it does not change the plan.
    expect((within(panel).getByRole("button", { name: /Sugerencia 1/ }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it.each([
    ["the switch is off", configRoutes(false)],
    ["the switch cannot be read", { "GET /api/v1/config/features": jsonResponse({ title: "Error" }, 500) }],
  ])("renders no suggestion control when %s", async (_case, routes) => {
    stubFetch(routes);
    const api = createApiClient({ fetch: fetchMock });
    const store = createEditorStore();
    store.getState().load(REVISION_ID, ENTITIES);
    render(
      <>
        <BatchPanel api={api} store={store} plan={createPlanStore({ userId: USER_ID, lease: OWN_LEASE })} />
        <EditorCanvas
          catalog={CATALOG}
          store={store}
          width={400}
          height={300}
          suggestionLayer={<SuggestionLayer store={store} api={api} />}
        />
      </>,
    );
    await waitFor(() => expect(sent("GET", "/api/v1/config/features").length).toBeGreaterThan(0));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole("button", { name: /Sugerencias/ })).toBeNull();
    expect(screen.queryByTestId("suggestion-layer")).toBeNull();
    expect(document.querySelector("[data-suggestion]")).toBeNull();
    expect(sent("GET", "/api/v1/config/params")).toHaveLength(0);
  });
});

describe("EditorPage with AI assistance", () => {
  afterEach(() => {
    cleanup();
    authStore.reset();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  async function renderEditor(enabled: boolean) {
    await signInAs("technician", {
      ...configRoutes(enabled),
      [`GET /api/v1/plans/${PLAN_ID}`]: jsonResponse(planOut()),
      "GET /api/v1/catalog/active": jsonResponse(catalogJson),
      [`GET ${ENTITIES_URL}`]: jsonResponse({ entities: ENTITIES }),
      [`GET /api/v1/sources/${SOURCE_ID}`]: jsonResponse({ id: SOURCE_ID, width_px: null, height_px: null }),
      [`POST /api/v1/plans/${PLAN_ID}/lease`]: jsonResponse(OWN_LEASE),
      [`DELETE /api/v1/plans/${PLAN_ID}/lease`]: jsonResponse({ released: true }),
    });
    const api = createApiClient({ fetch: fetchMock });
    render(
      <MemoryRouter initialEntries={[planEditorPath(CASE_ID, PLAN_ID)]}>
        <RouterRoutes>
          <Route
            path="/cases/:caseId/plans/:planId/editor"
            element={
              <EditorPage
                api={api}
                store={createEditorStore()}
                plan={createPlanStore()}
                width={400}
                height={300}
              />
            }
          />
        </RouterRoutes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: "Plano 1" });
  }

  it("shows the batch panel and the suggestion layer when the switch is on", async () => {
    await renderEditor(true);
    expect(await screen.findByRole("button", { name: BATCH_TEXT.toggle(3) })).toBeTruthy();
    expect(screen.getByTestId("suggestion-layer").querySelectorAll("[data-suggestion]")).toHaveLength(3);
  });

  it("renders none of the suggestion controls when the switch is off", async () => {
    await renderEditor(false);
    await waitFor(() => expect(sent("GET", "/api/v1/config/features").length).toBeGreaterThan(0));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole("button", { name: /Sugerencias/ })).toBeNull();
    expect(screen.queryByTestId("suggestion-layer")).toBeNull();
    expect(document.querySelector("[data-suggestion]")).toBeNull();
    // The manual entity is still drawn.
    expect(document.querySelector(`[data-entity-id="${MANUAL}"]`)).not.toBeNull();
  });
});
