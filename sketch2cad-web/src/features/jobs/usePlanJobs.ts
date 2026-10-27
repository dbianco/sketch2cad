// Jobs of a plan followed by the editor (`GET /plans/{plan_id}/jobs`): the `rectify` job queued
// when a calibration is accepted and the `suggest_lines` job chained after it. Both finish a few
// seconds after the editor opens, so the hook polls every `pollMs` while one of them is queued or
// running, gives up after `capMs` (then `stalled` is true), and stops on unmount or on the first
// failed request (the editor keeps working without this information).
//
// `onSucceeded` is called once for each job that is seen to finish: a job first seen queued or
// running that is now succeeded, or a job that appeared after the first answer already succeeded
// (it ran between two polls). Jobs already succeeded in the first answer are not reported: the
// caller loads its data after starting the hook, so that data already includes their results.
import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../../api/client";
import type { components } from "../../api/schema";

export type PlanJob = components["schemas"]["PlanJobOut"];

export const PLAN_JOBS_POLL_MS = 1500;
export const PLAN_JOBS_CAP_MS = 60_000;
export const RECTIFY_JOB_KIND = "rectify";
export const SUGGEST_JOB_KIND = "suggest_lines";
/** Kinds the editor follows after a calibration. */
export const FOLLOWED_JOB_KINDS: readonly string[] = [RECTIFY_JOB_KIND, SUGGEST_JOB_KIND];

export function isActiveJob(job: Pick<PlanJob, "status">): boolean {
  return job.status === "queued" || job.status === "running";
}

export interface PlanJobsOptions {
  onSucceeded?: (job: PlanJob) => void;
  pollMs?: number;
  capMs?: number;
}

export interface PlanJobs {
  /** Latest answer, newest first; empty until the first answer. */
  jobs: PlanJob[];
  /** Polling stopped at the cap while a job was still queued or running. */
  stalled: boolean;
}

export function usePlanJobs(
  api: Pick<ApiClient, "get">,
  planId: string,
  { onSucceeded, pollMs = PLAN_JOBS_POLL_MS, capMs = PLAN_JOBS_CAP_MS }: PlanJobsOptions = {},
): PlanJobs {
  const [state, setState] = useState<PlanJobs>({ jobs: [], stalled: false });
  const succeeded = useRef(onSucceeded);
  useEffect(() => {
    succeeded.current = onSucceeded;
  }, [onSucceeded]);

  useEffect(() => {
    if (planId === "") return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();
    const seen = new Map<string, PlanJob["status"]>();
    let first = true;

    async function poll(): Promise<void> {
      let jobs: PlanJob[];
      try {
        ({ jobs } = await api.get("/api/v1/plans/{plan_id}/jobs", {
          path: { plan_id: planId },
          query: { kind: [...FOLLOWED_JOB_KINDS] },
        }));
      } catch {
        return;
      }
      if (disposed) return;
      const finished = jobs.filter((job) => {
        if (job.status !== "succeeded") return false;
        const before = seen.get(job.id);
        return before === undefined ? !first : before !== "succeeded";
      });
      for (const job of jobs) seen.set(job.id, job.status);
      first = false;
      const active = jobs.some(isActiveJob);
      const stalled = active && Date.now() - startedAt >= capMs;
      setState({ jobs, stalled });
      for (const job of finished) succeeded.current?.(job);
      if (active && !stalled) timer = setTimeout(() => void poll(), pollMs);
    }

    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [api, planId, pollMs, capMs]);

  return state;
}
