// The editor page follows the calibration's jobs: reload when suggestions arrive, failure notice.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes as RouterRoutes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import catalogJson from "../../editor/fixtures/catalog-default-1.json";
import { createPlanStore, type PlanOut } from "../../editor/plan/planStore";
import { createEditorStore } from "../../editor/store/entities";
import type { EntityOut } from "../../editor/store/types";
import { authStore } from "../auth/authStore";
import { planCalibrationPath, planEditorPath } from "../calibration/CalibrationPage";
import { JOB_FAILURE_TEXT } from "../jobs/JobFailure";
import type { PlanJob } from "../jobs/usePlanJobs";
import { EditorPage } from "./EditorPage";
import { v12Message } from "./messages";
import { PLAN_JOBS_TEXT } from "./PlanJobsNotice";
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

const POLL_MS = 15;
const CALIBRATION_ID = "00000000-0000-4000-8000-0000000000f1";
const JOBS_URL = `/api/v1/plans/${PLAN_ID}/jobs`;
const LEASE_URL = `/api/v1/plans/${PLAN_ID}/lease`;
const ENTITIES_URL = `/api/v1/revisions/${REVISION_ID}/entities`;
const OWN_LEASE = { user_id: USER_ID, display_name: "technician", expires_at: "2999-01-01T00:00:00Z" };
const TOO_LARGE = {
  cause: "La imagen rectificada sería demasiado grande para generarse.",
  action: "Revisa las dimensiones conocidas o los puntos de calibración y vuelve a aceptarla.",
};

const calibratedPlan = (): PlanOut =>
  planOut({
    calibration: {
      id: CALIBRATION_ID,
      accepted_at: "2026-10-27T00:00:00Z",
      accepted_by: USER_ID,
      as_metric: false,
      control_points: [],
      image_to_paper: [[0.1, 0, 0], [0, 0.1, 0], [0, 0, 1]],
      local_frame: null,
      method: "scan_segment",
      paper_scale: 100,
      reprojection_error_mm: null,
    },
  });

function job(id: string, kind: string, status: PlanJob["status"], error: PlanJob["error"] = null): PlanJob {
  return {
    id,
    kind,
    status,
    source_id: SOURCE_ID,
    error,
    result_ref: null,
    result: null,
    created_at: "2026-10-27T12:00:00Z",
    calibration_id: CALIBRATION_ID,
  };
}

function suggestion(id: string): EntityOut {
  return {
    id,
    type: "polyline",
    layer: "L_STRUCTURE",
    geometry: { kind: "polyline", coords_m: [[0, 0], [2, 0]], closed: false },
    attributes: {},
    confidence: 0.9,
    constraint: null,
    created_by: null,
    deleted_at: null,
    discrepancy: null,
    evidence_id: null,
    image_region: null,
    metric_origin: "trace_only",
    model_version: "vision-1",
    review_status: "suggested",
    revision_id: REVISION_ID,
    revision_number: 1,
    source: { method: "cv" },
    updated_at: "2026-10-27T00:00:00Z",
  };
}

/** A route answering `answers` in turn and then repeating the last one. */
function sequence(answers: (() => Response)[]): () => Response {
  let call = 0;
  return () => {
    const answer = answers[Math.min(call, answers.length - 1)];
    call += 1;
    return answer ? answer() : jsonResponse({}, 500);
  };
}

const jobsAnswer = (...jobs: PlanJob[]) => () => jsonResponse({ jobs });

async function renderEditor(routes: Routes) {
  await signInAs("technician", {
    [`GET /api/v1/plans/${PLAN_ID}`]: jsonResponse(calibratedPlan()),
    "GET /api/v1/catalog/active": jsonResponse(catalogJson),
    [`GET ${ENTITIES_URL}`]: jsonResponse({ entities: [] }),
    [`GET /api/v1/sources/${SOURCE_ID}`]: jsonResponse({ id: SOURCE_ID, width_px: null, height_px: null }),
    [`POST ${LEASE_URL}`]: jsonResponse(OWN_LEASE),
    [`DELETE ${LEASE_URL}`]: jsonResponse({ released: true }),
    ...routes,
  });
  const store = createEditorStore();
  const plan = createPlanStore();
  render(
    <MemoryRouter initialEntries={[planEditorPath(CASE_ID, PLAN_ID)]}>
      <RouterRoutes>
        <Route
          path="/cases/:caseId/plans/:planId/editor"
          element={
            <EditorPage store={store} plan={plan} syncDelayMs={10} jobsPollMs={POLL_MS} width={400} height={300} />
          }
        />
        <Route path="/cases/:caseId/plans/:planId/calibration" element={<p>Calibración</p>} />
      </RouterRoutes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Plano 1" });
  return { store, plan };
}

const jobPolls = () => sent("GET", JOBS_URL).length;
const entityLoads = () => sent("GET", ENTITIES_URL).length;

describe("EditorPage and the calibration's jobs", () => {
  afterEach(() => {
    cleanup();
    authStore.reset();
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("reloads the entities once when the chained suggestion job succeeds", async () => {
    const rectifyDone = job("r", "rectify", "succeeded");
    const { store } = await renderEditor({
      [`GET ${JOBS_URL}`]: sequence([
        jobsAnswer(job("r", "rectify", "queued")),
        jobsAnswer(job("r", "rectify", "running")),
        jobsAnswer(job("s", "suggest_lines", "queued"), rectifyDone),
        jobsAnswer(job("s", "suggest_lines", "running"), rectifyDone),
        jobsAnswer(job("s", "suggest_lines", "succeeded"), rectifyDone),
      ]),
      [`GET ${ENTITIES_URL}`]: sequence([
        () => jsonResponse({ entities: [] }),
        () => jsonResponse({ entities: [suggestion("s-1"), suggestion("s-2")] }),
      ]),
    });
    expect(await screen.findByText(PLAN_JOBS_TEXT.rectifying)).toBeTruthy();

    await waitFor(() => expect(store.getState().entities.order).toHaveLength(2));
    expect(entityLoads()).toBe(2);
    expect(jobPolls()).toBe(5);
    expect(screen.queryByText(PLAN_JOBS_TEXT.suggesting)).toBeNull();
    // Polling stopped; no further reload.
    await new Promise((resolve) => setTimeout(resolve, POLL_MS * 6));
    expect(jobPolls()).toBe(5);
    expect(entityLoads()).toBe(2);
  });

  it("shows the failed rectification's cause and action with a link back to calibration", async () => {
    await renderEditor({
      [`GET ${JOBS_URL}`]: sequence([
        jobsAnswer(job("r", "rectify", "running")),
        jobsAnswer(job("r", "rectify", "failed", TOO_LARGE)),
      ]),
    });

    const alert = (await screen.findByText(PLAN_JOBS_TEXT.rectifyFailed)).closest('[role="alert"]');
    if (alert === null) throw new Error("the failure is not an alert");
    expect(alert.textContent).toContain(`${JOB_FAILURE_TEXT.cause} ${TOO_LARGE.cause}`);
    expect(alert.textContent).toContain(`${JOB_FAILURE_TEXT.action} ${TOO_LARGE.action}`);
    const link = screen.getByRole("link", { name: PLAN_JOBS_TEXT.recalibrate });
    expect(link.getAttribute("href")).toBe(planCalibrationPath(CASE_ID, PLAN_ID));
    expect(entityLoads()).toBe(1);

    fireEvent.click(link);
    expect(await screen.findByText("Calibración")).toBeTruthy();
  });

  it("does not show the failure of an earlier calibration", async () => {
    const old = { ...job("r", "rectify", "failed", TOO_LARGE), calibration_id: "old-calibration" };
    await renderEditor({ [`GET ${JOBS_URL}`]: jsonResponse({ jobs: [old] }) });

    await waitFor(() => expect(jobPolls()).toBe(1));
    expect(screen.queryByText(PLAN_JOBS_TEXT.rectifyFailed)).toBeNull();
  });

  it("does not reload while the local queue has unsynced edits", async () => {
    const conflict = () =>
      jsonResponse(
        { title: "Conflict", status: 409, detail: v12Message("Ana"), code: "V-12", holder: OWN_LEASE },
        409,
      );
    const { store } = await renderEditor({
      // Without the lease the queue is never sent, so it stays non-empty.
      [`POST ${LEASE_URL}`]: conflict,
      [`GET ${JOBS_URL}`]: sequence([
        jobsAnswer(job("s", "suggest_lines", "running")),
        jobsAnswer(job("s", "suggest_lines", "running")),
        jobsAnswer(job("s", "suggest_lines", "succeeded")),
      ]),
    });
    act(() => {
      store.getState().addEntity({
        type: "line",
        layer: "L_STRUCTURE",
        geometry: { kind: "line", coords_m: [[0, 0], [1, 0]] },
      });
    });

    await waitFor(() => expect(jobPolls()).toBe(3));
    await new Promise((resolve) => setTimeout(resolve, POLL_MS * 4));
    expect(store.getState().queue.ops).toHaveLength(1);
    expect(entityLoads()).toBe(1);
  });
});
