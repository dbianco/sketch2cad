// Export page of a plan (route /cases/:caseId/plans/:planId/export, prototype screen 6, US-15,
// US-16, REQ-14). It loads the plan and its artifacts and offers a draft export (EDITING or later)
// and a final export (VALIDATED only, V-11; the API also requires `deliver` on the case file and
// answers 403 otherwise, since the web does not know the user's case permissions). The paper and
// scale selectors default to "auto": the server then proposes the sheet and the largest scale that
// fits. The export job is polled every JOB_POLL_INTERVAL_MS until it succeeds (the plan and the
// artifact list are reloaded) or fails (its Spanish cause and action are shown).
import { useCallback, useEffect, useId, useState, type CSSProperties } from "react";
import { Link, useParams } from "react-router";
import { api as defaultApi, type ApiClient } from "../../api/client";
import type { components } from "../../api/schema";
import type { PlanOut } from "../../editor/plan/planStore";
import { planEditorPath } from "../calibration/CalibrationPage";
import { PLAN_STATUS_LABELS } from "../plans/messages";
import { planReviewPath } from "../plans/ReviewPage";
import { ArtifactTable } from "./ArtifactTable";
import {
  DISCLAIMER_TEXT,
  DRAFT_NOT_ALLOWED_MESSAGE,
  DRAFT_STATUSES,
  EXPORT_MESSAGES as M,
  JOB_STATUS_LABELS,
  PAPER_OPTIONS,
  SCALE_OPTIONS,
  V11_MESSAGE,
  exportErrorMessage,
  type ArtifactOut,
} from "./messages";

type ExportIn = components["schemas"]["ExportIn"];
type JobOut = components["schemas"]["JobOut"];
type Paper = (typeof PAPER_OPTIONS)[number]["value"];
type Scale = (typeof SCALE_OPTIONS)[number]["value"];

/** Delay between two `GET /jobs/{id}` requests while the export job is not finished. */
export const JOB_POLL_INTERVAL_MS = 2000;

/** Body of `POST /plans/{id}/exports`; "auto" scale is sent as `null` (server proposal). */
export function exportBody(mode: ExportIn["mode"], paper: Paper, scale: Scale): ExportIn {
  return { mode, paper, scale: scale === "auto" ? null : (Number(scale) as NonNullable<ExportIn["scale"]>) };
}

const styles = {
  page: { padding: 32, display: "flex", flexDirection: "column", gap: 20, maxWidth: 1200 },
  card: { background: "#fff", border: "1px solid #D5DCE3", borderRadius: 8, padding: 16, display: "flex", flexDirection: "column", gap: 8 },
  notice: { background: "#FFF4DC", border: "1px solid #E5B85C", borderRadius: 8, padding: "10px 14px", margin: 0, lineHeight: 1.5 },
  hint: { margin: 0, fontSize: 13, color: "#33414E" },
} satisfies Record<string, CSSProperties>;

type Loaded = { kind: "loading" } | { kind: "error" } | { kind: "ready"; plan: PlanOut; artifacts: ArtifactOut[] | null };

export interface ExportPageProps {
  api?: Pick<ApiClient, "get" | "post">;
}

export function ExportPage({ api = defaultApi }: ExportPageProps) {
  const { caseId = "", planId = "" } = useParams();
  const [state, setState] = useState<Loaded>({ kind: "loading" });
  const [version, setVersion] = useState(0);
  const [paper, setPaper] = useState<Paper>("auto");
  const [scale, setScale] = useState<Scale>("auto");
  const [job, setJob] = useState<JobOut | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ids = { paper: useId(), scale: useId(), scaleHint: useId(), draftNote: useId(), finalNote: useId() };

  useEffect(() => {
    let active = true;
    Promise.all([
      api.get("/api/v1/plans/{plan_id}", { path: { plan_id: planId } }),
      api.get("/api/v1/plans/{plan_id}/artifacts", { path: { plan_id: planId } }).catch(() => null),
    ]).then(
      ([plan, artifacts]) => active && setState({ kind: "ready", plan, artifacts }),
      () => active && setState({ kind: "error" }),
    );
    return () => {
      active = false;
    };
  }, [api, planId, version]);

  const pending = job !== null && (job.status === "queued" || job.status === "running");

  const poll = useCallback(
    (id: string, signal: AbortSignal) =>
      api.get("/api/v1/jobs/{job_id}", { path: { job_id: id }, signal }).then((next) => {
        if (signal.aborted) return;
        setJob(next);
        if (next.status === "succeeded") setVersion((v) => v + 1);
      }),
    [api],
  );

  // One timer per job snapshot: every poll result is a new `job`, which schedules the next poll.
  useEffect(() => {
    if (job === null || job.status === "succeeded" || job.status === "failed") return;
    const controller = new AbortController();
    const jobId = job.id;
    const timer = setTimeout(() => {
      poll(jobId, controller.signal).catch(() => {
        if (!controller.signal.aborted) {
          setJob(null);
          setMessage(M.jobUnreachable);
        }
      });
    }, JOB_POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [job, poll]);

  async function startExport(mode: ExportIn["mode"]): Promise<void> {
    setBusy(true);
    setMessage(null);
    setJob(null);
    try {
      const queued = await api.post("/api/v1/plans/{plan_id}/exports", {
        path: { plan_id: planId },
        body: exportBody(mode, paper, scale),
      });
      setJob(queued);
      if (queued.status === "succeeded") setVersion((v) => v + 1);
    } catch (error) {
      setMessage(exportErrorMessage(error, M.exportFailed));
    } finally {
      setBusy(false);
    }
  }

  if (state.kind === "loading") return <p role="status">{M.loading}</p>;
  if (state.kind === "error") return <p role="alert">{M.loadFailed}</p>;
  const { plan, artifacts } = state;
  const draftAllowed = DRAFT_STATUSES.has(plan.status);
  const finalAllowed = plan.status === "VALIDATED";
  const revision = plan.current_revision === null ? "" : ` · Revisión ${String(plan.current_revision.number)}`;

  return (
    <section aria-label={M.title} style={styles.page}>
      <header style={{ display: "flex", gap: 16, alignItems: "baseline", flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 26 }}>
          {M.title} · {plan.title}
        </h1>
        <span>
          {PLAN_STATUS_LABELS[plan.status]}
          {revision}
        </span>
        <Link to={planReviewPath(caseId, plan.id)}>{M.backToReview}</Link>
        <Link to={planEditorPath(caseId, plan.id)}>{M.openEditor}</Link>
      </header>

      <fieldset style={{ ...styles.card, flexDirection: "row", flexWrap: "wrap", gap: 16 }}>
        <legend>{M.options}</legend>
        <label htmlFor={ids.paper}>{M.paper}</label>
        <select id={ids.paper} value={paper} onChange={(event) => setPaper(event.target.value as Paper)}>
          {PAPER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <label htmlFor={ids.scale}>{M.scale}</label>
        <select
          id={ids.scale}
          value={scale}
          aria-describedby={ids.scaleHint}
          onChange={(event) => setScale(event.target.value as Scale)}
        >
          {SCALE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <p id={ids.scaleHint} style={{ ...styles.hint, flexBasis: "100%" }}>
          {M.scaleHint}
        </p>
      </fieldset>

      <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
        <section aria-labelledby={`${ids.draftNote}-title`} style={{ ...styles.card, flex: "1 1 320px" }}>
          <h2 id={`${ids.draftNote}-title`} style={{ margin: 0, fontSize: 18 }}>
            {M.draftTitle}
          </h2>
          <button
            type="button"
            disabled={busy || pending || !draftAllowed}
            aria-describedby={ids.draftNote}
            onClick={() => void startExport("draft")}
          >
            {M.draftButton}
          </button>
          <p id={ids.draftNote} style={styles.hint}>
            {draftAllowed ? M.draftNote : DRAFT_NOT_ALLOWED_MESSAGE}
          </p>
        </section>
        <section aria-labelledby={`${ids.finalNote}-title`} style={{ ...styles.card, flex: "1 1 320px" }}>
          <h2 id={`${ids.finalNote}-title`} style={{ margin: 0, fontSize: 18 }}>
            {M.finalTitle}
          </h2>
          <button
            type="button"
            disabled={busy || pending || !finalAllowed}
            aria-describedby={ids.finalNote}
            onClick={() => void startExport("final")}
          >
            {M.finalButton}
          </button>
          <p id={ids.finalNote} style={styles.hint}>
            {finalAllowed ? M.finalNote : V11_MESSAGE}
          </p>
          <p style={styles.notice}>{DISCLAIMER_TEXT}</p>
        </section>
      </div>

      <div role="status" aria-live="polite">
        {job !== null && <p style={{ margin: 0 }}>{JOB_STATUS_LABELS[job.status]}</p>}
        {job?.status === "failed" && job.error !== null && (
          <p style={{ margin: 0 }}>
            {job.error.cause} {job.error.action}
          </p>
        )}
      </div>
      {message !== null && <p role="alert">{message}</p>}

      {artifacts === null ? <p role="alert">{M.artifactsFailed}</p> : <ArtifactTable artifacts={artifacts} api={api} />}
    </section>
  );
}
