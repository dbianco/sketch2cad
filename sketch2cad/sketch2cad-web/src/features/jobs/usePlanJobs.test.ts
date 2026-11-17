import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../../api/client";
import { PLAN_JOBS_CAP_MS, PLAN_JOBS_POLL_MS, usePlanJobs, type PlanJob } from "./usePlanJobs";

const PLAN_ID = "00000000-0000-4000-8000-0000000000a1";
const CALIBRATION_ID = "00000000-0000-4000-8000-0000000000f1";

function job(id: string, kind: string, status: PlanJob["status"]): PlanJob {
  return {
    id,
    kind,
    status,
    source_id: "00000000-0000-4000-8000-0000000000d1",
    error: null,
    result_ref: null,
    result: null,
    created_at: "2026-10-27T12:00:00Z",
    calibration_id: CALIBRATION_ID,
  };
}

/** API whose `GET /plans/{id}/jobs` answers `answers` in turn, repeating the last one. */
function apiAnswering(answers: (PlanJob[] | Error)[]) {
  let call = 0;
  const get = vi.fn(() => {
    const answer = answers[Math.min(call, answers.length - 1)];
    call += 1;
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve({ jobs: answer ?? [] });
  });
  return { api: { get } as unknown as Pick<ApiClient, "get">, get };
}

async function tick(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("usePlanJobs", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("reports the suggestion job once when it goes queued -> running -> succeeded", async () => {
    const rectifyDone = job("r", "rectify", "succeeded");
    const { api, get } = apiAnswering([
      [job("r", "rectify", "running")],
      [job("s", "suggest_lines", "queued"), rectifyDone],
      [job("s", "suggest_lines", "running"), rectifyDone],
      [job("s", "suggest_lines", "succeeded"), rectifyDone],
    ]);
    const onSucceeded = vi.fn();
    const { result } = renderHook(() => usePlanJobs(api, PLAN_ID, { onSucceeded }));

    await tick();
    expect(get).toHaveBeenCalledWith("/api/v1/plans/{plan_id}/jobs", {
      path: { plan_id: PLAN_ID },
      query: { kind: ["rectify", "suggest_lines"] },
    });
    for (let n = 0; n < 3; n += 1) await tick(PLAN_JOBS_POLL_MS);

    expect(get).toHaveBeenCalledTimes(4);
    expect(onSucceeded.mock.calls.map(([j]) => (j as PlanJob).kind)).toEqual([
      "rectify",
      "suggest_lines",
    ]);
    expect(result.current.jobs[0]?.status).toBe("succeeded");
    // Nothing is queued or running any more: polling stopped.
    await tick(PLAN_JOBS_POLL_MS * 5);
    expect(get).toHaveBeenCalledTimes(4);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reports a job that appeared and finished between two polls", async () => {
    const { api } = apiAnswering([
      [job("r", "rectify", "running")],
      [job("s", "suggest_lines", "succeeded"), job("r", "rectify", "succeeded")],
    ]);
    const onSucceeded = vi.fn();
    renderHook(() => usePlanJobs(api, PLAN_ID, { onSucceeded }));

    await tick();
    await tick(PLAN_JOBS_POLL_MS);

    expect(onSucceeded.mock.calls.map(([j]) => (j as PlanJob).id).sort()).toEqual(["r", "s"]);
  });

  it("does not report jobs already finished in the first answer and does not poll", async () => {
    const { api, get } = apiAnswering([[job("s", "suggest_lines", "succeeded")]]);
    const onSucceeded = vi.fn();
    renderHook(() => usePlanJobs(api, PLAN_ID, { onSucceeded }));

    await tick();
    await tick(PLAN_JOBS_POLL_MS * 3);

    expect(get).toHaveBeenCalledTimes(1);
    expect(onSucceeded).not.toHaveBeenCalled();
  });

  it("stops at the cap and says so", async () => {
    const { api, get } = apiAnswering([[job("r", "rectify", "running")]]);
    const { result } = renderHook(() => usePlanJobs(api, PLAN_ID, { capMs: PLAN_JOBS_POLL_MS * 3 }));

    await tick();
    for (let n = 0; n < 6; n += 1) await tick(PLAN_JOBS_POLL_MS);

    expect(get).toHaveBeenCalledTimes(4);
    expect(result.current.stalled).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(PLAN_JOBS_CAP_MS).toBe(60_000);
  });

  it("stops polling on unmount", async () => {
    const { api, get } = apiAnswering([[job("r", "rectify", "running")]]);
    const { unmount } = renderHook(() => usePlanJobs(api, PLAN_ID));

    await tick();
    await tick(PLAN_JOBS_POLL_MS);
    expect(get).toHaveBeenCalledTimes(2);
    unmount();

    await tick(PLAN_JOBS_POLL_MS * 5);
    expect(get).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("gives up quietly when the request fails", async () => {
    const { api, get } = apiAnswering([new Error("network")]);
    const { result } = renderHook(() => usePlanJobs(api, PLAN_ID));

    await tick();
    await tick(PLAN_JOBS_POLL_MS * 3);

    expect(get).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({ jobs: [], stalled: false });
  });
});
