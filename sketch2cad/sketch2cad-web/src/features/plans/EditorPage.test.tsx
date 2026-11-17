import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes as RouterRoutes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import catalogJson from "../../editor/fixtures/catalog-default-1.json";
import { createPlanStore, type PlanOut } from "../../editor/plan/planStore";
import { createEditorStore } from "../../editor/store/entities";
import { authStore } from "../auth/authStore";
import { planEditorPath } from "../calibration/CalibrationPage";
import { EditorPage, imageBoundsOf } from "./EditorPage";
import { V10_MESSAGE, v12Message } from "./messages";
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
  type Routes,
} from "./testing";

const LEASE_URL = `/api/v1/plans/${PLAN_ID}/lease`;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const OWN_LEASE = { user_id: USER_ID, display_name: "technician", expires_at: "2999-01-01T00:00:00Z" };
const HOLDER = { user_id: "someone-else", display_name: "Ana Pérez", expires_at: "2999-01-01T00:00:00Z" };
const V12_CONFLICT = () =>
  jsonResponse(
    { title: "Conflict", status: 409, detail: v12Message("Ana Pérez"), code: "V-12", holder: HOLDER },
    409,
  );

function editorRoutes(plan: PlanOut, extra: Routes = {}): Routes {
  return {
    [`GET /api/v1/plans/${PLAN_ID}`]: jsonResponse(plan),
    "GET /api/v1/catalog/active": jsonResponse(catalogJson),
    [`GET ${ENTITIES_URL}`]: jsonResponse({ entities: [] }),
    [`GET /api/v1/sources/${SOURCE_ID}`]: jsonResponse({ id: SOURCE_ID, width_px: null, height_px: null }),
    [`POST ${LEASE_URL}`]: jsonResponse(OWN_LEASE),
    [`DELETE ${LEASE_URL}`]: jsonResponse({ released: true }),
    ...extra,
  };
}

async function renderEditor(plan: PlanOut, extra: Routes = {}) {
  await signInAs("technician", editorRoutes(plan, extra));
  const store = createEditorStore();
  const planState = createPlanStore();
  const view = render(
    <MemoryRouter initialEntries={[planEditorPath(CASE_ID, PLAN_ID)]}>
      <RouterRoutes>
        <Route
          path="/cases/:caseId/plans/:planId/editor"
          element={<EditorPage store={store} plan={planState} syncDelayMs={10} width={400} height={300} />}
        />
      </RouterRoutes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Plano 1" });
  return { store, planState, view };
}

function addLine(store: ReturnType<typeof createEditorStore>): void {
  act(() => {
    store.getState().addEntity({
      type: "line",
      layer: "L_STRUCTURE",
      geometry: { kind: "line", coords_m: [[0, 0], [1, 0]] },
    });
  });
}

function toolButtons(): HTMLButtonElement[] {
  const group = screen.getByRole("group", { name: "Herramientas" });
  return within(group).getAllByRole("button") as HTMLButtonElement[];
}

describe("EditorPage", () => {
  afterEach(() => {
    cleanup();
    authStore.reset();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("loads the plan, takes the lease and flushes the queue after an edit", async () => {
    const { store, planState } = await renderEditor(planOut(), {
      [`POST ${ENTITIES_URL}`]: jsonResponse({ entities: [{ id: "server-1" }] }, 201),
    });

    await waitFor(() => expect(planState.getState().lease).toEqual(OWN_LEASE));
    expect(planState.getState()).toMatchObject({ planId: PLAN_ID, revisionId: REVISION_ID, userId: USER_ID });
    expect(store.getState().revisionId).toBe(REVISION_ID);
    expect(toolButtons().every((b) => !b.disabled)).toBe(true);

    addLine(store);
    expect(screen.getByText("Cambios pendientes de sincronizar")).toBeTruthy();
    await screen.findByText("Sincronizado");
    expect(sent("POST", ENTITIES_URL)).toHaveLength(1);
    expect(store.getState().queue.ops).toEqual([]);
  });

  it("is read-only with the V-12 banner when the lease is refused with 409", async () => {
    await renderEditor(planOut(), { [`POST ${LEASE_URL}`]: V12_CONFLICT });

    expect(
      (await screen.findByText(v12Message("Ana Pérez"))).textContent,
    ).toBe("Ana Pérez está editando este plano. Entra en solo lectura o espera a que termine.");
    expect(toolButtons().every((b) => b.disabled)).toBe(true);
  });

  it("turns read-only when the sync gets a 409", async () => {
    const { store } = await renderEditor(planOut(), { [`POST ${ENTITIES_URL}`]: V12_CONFLICT });
    await waitFor(() => expect(sent("POST", LEASE_URL)).toHaveLength(1));
    await waitFor(() => expect(toolButtons()[0]?.disabled).toBe(false));

    addLine(store);

    await screen.findByText(v12Message("Ana Pérez"));
    expect(screen.getByText("Error al sincronizar los cambios")).toBeTruthy();
    expect(toolButtons().every((b) => b.disabled)).toBe(true);
  });

  it("releases the lease when the editor is left", async () => {
    const { planState, view } = await renderEditor(planOut());
    await waitFor(() => expect(planState.getState().lease).toEqual(OWN_LEASE));

    view.unmount();

    await waitFor(() => expect(sent("DELETE", LEASE_URL)).toHaveLength(1));
  });

  it("opens a validated plan read-only with V-10 and without taking the lease", async () => {
    await renderEditor(planOut({ status: "VALIDATED" }));

    expect(screen.getByText(V10_MESSAGE)).toBeTruthy();
    expect(toolButtons().every((b) => b.disabled)).toBe(true);
    expect(sent("POST", LEASE_URL)).toEqual([]);
  });

  it("places the source image through the calibration homography", () => {
    const plan = planOut({
      calibration: {
        id: "k1",
        accepted_at: "2026-10-25T00:00:00Z",
        accepted_by: USER_ID,
        as_metric: true,
        control_points: [],
        // 0.1 mm per pixel.
        image_to_paper: [[0.1, 0, 0], [0, 0.1, 0], [0, 0, 1]],
        local_frame: null,
        method: "scan_dpi",
        paper_scale: 50,
        reprojection_error_mm: null,
      },
    });

    // 2000 x 1000 px = 200 x 100 mm on paper = 10 x 5 m at 1:50, paper Y down -> world Y up.
    const bounds = imageBoundsOf(plan, 2000, 1000);
    expect(bounds?.minX).toBeCloseTo(0);
    expect(bounds?.maxX).toBeCloseTo(10);
    expect(bounds?.minY).toBeCloseTo(-5);
    expect(bounds?.maxY).toBeCloseTo(0);
  });
});
