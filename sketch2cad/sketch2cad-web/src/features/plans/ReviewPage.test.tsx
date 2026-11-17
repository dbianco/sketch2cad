import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes as RouterRoutes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PlanOut } from "../../editor/plan/planStore";
import { authStore } from "../auth/authStore";
import { V09_VALIDATE_MESSAGE, V10_MESSAGE } from "./messages";
import { ReviewPage, planReviewPath } from "./ReviewPage";
import {
  CASE_ID,
  PLAN_ID,
  REVISION_ID,
  fetchMock,
  jsonResponse,
  planOut,
  sent,
  signInAs,
  type Routes,
} from "./testing";
import type { UserRole } from "./TransitionBar";

const PLAN_URL = `/api/v1/plans/${PLAN_ID}`;
const TRANSITION_URL = `${PLAN_URL}/transition`;

function entity(layer: string, id: string) {
  return { id, layer, type: "line", geometry: { kind: "line", coords_m: [[0, 0], [1, 0]] } };
}

/** Routes of the page; a `transition` plan becomes the plan the API returns afterwards. */
function reviewRoutes(plan: PlanOut, transition?: PlanOut | Response): Routes {
  let current = plan;
  return {
    [`GET ${PLAN_URL}`]: () => jsonResponse(current),
    [`POST ${TRANSITION_URL}`]: () => {
      if (transition instanceof Response) return transition.clone();
      current = transition ?? current;
      return jsonResponse(current);
    },
    [`GET /api/v1/revisions/${REVISION_ID}/entities`]: jsonResponse({
      entities: [entity("L_STRUCTURE", "e1"), entity("L_STRUCTURE", "e2"), entity("L_DOORS", "e3")],
    }),
    [`GET /api/v1/case-files/${CASE_ID}/events`]: jsonResponse({
      items: [
        {
          id: 7,
          kind: "plan.transition",
          occurred_at: "2026-10-25T14:19:00Z",
          reason: null,
        },
      ],
      next_cursor: null,
    }),
  };
}

async function renderReview(
  role: UserRole,
  plan: PlanOut,
  transition?: PlanOut | Response,
): Promise<void> {
  await signInAs(role, reviewRoutes(plan, transition));
  render(
    <MemoryRouter initialEntries={[planReviewPath(CASE_ID, PLAN_ID)]}>
      <RouterRoutes>
        <Route path="/cases/:caseId/plans/:planId/review" element={<ReviewPage />} />
      </RouterRoutes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: /Revisión del plano/ });
}

const button = (name: string) => screen.queryByRole("button", { name });

describe("ReviewPage", () => {
  afterEach(() => {
    cleanup();
    authStore.reset();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("summarises the plan, its entities by layer and its events", async () => {
    await renderReview("reviewer", planOut({ status: "IN_REVIEW" }));

    expect(screen.getByText("En revisión")).toBeTruthy();
    expect(screen.getByText("Aproximado")).toBeTruthy();
    expect(screen.getByText("L_STRUCTURE: 2")).toBeTruthy();
    expect(screen.getByText("L_DOORS: 1")).toBeTruthy();
    expect(await screen.findByText(/plan\.transition/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Abrir el editor" }).getAttribute("href")).toBe(
      `/cases/${CASE_ID}/plans/${PLAN_ID}/editor`,
    );
  });

  it("keeps Reject disabled until a non-blank reason is written (V-09)", async () => {
    await renderReview("reviewer", planOut({ status: "IN_REVIEW" }), planOut({ status: "EDITING" }));
    const reason = screen.getByRole("textbox", { name: "Motivo (obligatorio al rechazar)" });

    expect(button("Rechazar")?.hasAttribute("disabled")).toBe(true);
    fireEvent.change(reason, { target: { value: "   " } });
    expect(button("Rechazar")?.hasAttribute("disabled")).toBe(true);
    fireEvent.change(reason, { target: { value: "Falta la cota de la puerta" } });
    expect(button("Rechazar")?.hasAttribute("disabled")).toBe(false);

    fireEvent.click(button("Rechazar") as HTMLElement);
    await screen.findByText("En edición");
    expect(sent("POST", TRANSITION_URL)).toEqual([
      { to: "REJECTED", reason: "Falta la cota de la puerta" },
    ]);
  });

  it("validates the plan and shows API errors in Spanish", async () => {
    const forbidden = jsonResponse(
      { title: "Forbidden", status: 403, detail: V09_VALIDATE_MESSAGE, code: "V-09" },
      403,
    );
    await renderReview("reviewer", planOut({ status: "IN_REVIEW" }), forbidden);

    fireEvent.click(button("Validar") as HTMLElement);

    expect((await screen.findByRole("alert")).textContent).toBe(V09_VALIDATE_MESSAGE);
    expect(sent("POST", TRANSITION_URL)).toEqual([{ to: "VALIDATED" }]);
  });

  it("never shows Validate, Reject, Take review or Reopen to a technician", async () => {
    await renderReview("technician", planOut({ status: "IN_REVIEW" }));

    expect(button("Validar")).toBeNull();
    expect(button("Rechazar")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    cleanup();

    await renderReview("technician", planOut({ status: "REVIEW_PENDING" }));
    expect(button("Tomar revisión")).toBeNull();
    cleanup();

    await renderReview("technician", planOut({ status: "VALIDATED" }));
    expect(screen.getByText(V10_MESSAGE)).toBeTruthy();
    expect(button("Reabrir")).toBeNull();
  });

  it("lets a technician send an editing plan to review", async () => {
    await renderReview("technician", planOut({ status: "EDITING" }), planOut({ status: "REVIEW_PENDING" }));

    fireEvent.click(button("Enviar a revisión") as HTMLElement);

    await screen.findByText("Pendiente de revisión");
    expect(sent("POST", TRANSITION_URL)).toEqual([{ to: "REVIEW_PENDING" }]);
  });

  it("lets a reviewer take a pending review", async () => {
    await renderReview("reviewer", planOut({ status: "REVIEW_PENDING" }), planOut({ status: "IN_REVIEW" }));

    fireEvent.click(button("Tomar revisión") as HTMLElement);

    await screen.findByText("En revisión");
    expect(sent("POST", TRANSITION_URL)).toEqual([{ to: "IN_REVIEW" }]);
  });

  it("shows V-10 and Reopen to a reviewer of a validated plan", async () => {
    await renderReview("reviewer", planOut({ status: "VALIDATED" }), planOut({ status: "EDITING" }));

    expect(screen.getByText(V10_MESSAGE)).toBeTruthy();
    expect(button("Validar")).toBeNull();
    fireEvent.click(button("Reabrir") as HTMLElement);

    await screen.findByText("En edición");
    expect(sent("POST", TRANSITION_URL)).toEqual([{ to: "REOPENED" }]);
  });
});
