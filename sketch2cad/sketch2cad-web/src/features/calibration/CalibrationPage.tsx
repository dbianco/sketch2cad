import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api } from "../../api/client";
import type { components } from "../../api/schema";
import { CalibrationWizard, type Plan } from "./CalibrationWizard";
import { CALIBRATION_MESSAGES as M, SOURCE_MODE_LABELS, type SourceMode } from "./messages";
import { fieldStyles } from "./ScanSteps";

type Source = components["schemas"]["SourceOut"];

/** Route of the calibration of an existing plan; the plan id in the URL survives a reload. */
export function planCalibrationPath(caseId: string, planId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/plans/${encodeURIComponent(planId)}/calibration`;
}

/** Route of the plan editor (features/plans/EditorPage.tsx). */
export function planEditorPath(caseId: string, planId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/plans/${encodeURIComponent(planId)}/editor`;
}

const page = { padding: 32, display: "flex", flexDirection: "column", gap: 16, maxWidth: 420 } as const;

/**
 * Step 1 (US-04): choose the source mode, default `schematic_sketch`, and create the plan from
 * the source. There is no list-plans endpoint, so the new plan id goes into the URL.
 */
export function SourceCalibrationPage() {
  const { caseId = "", sourceId = "" } = useParams();
  const navigate = useNavigate();
  const [mode, setMode] = useState<SourceMode>("schematic_sketch");
  const [title, setTitle] = useState<string>(M.defaultPlanTitle);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function create(): Promise<void> {
    setBusy(true);
    setFailed(false);
    try {
      const plan = await api.post("/api/v1/case-files/{case_id}/plans", {
        path: { case_id: caseId },
        body: { title: title.trim() || M.defaultPlanTitle, primary_source_id: sourceId, source_mode: mode },
      });
      void navigate(planCalibrationPath(caseId, plan.id), { replace: true });
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <section aria-label={M.title} style={page}>
      <h1 style={{ margin: 0, fontSize: 26 }}>{M.title}</h1>
      <p style={fieldStyles.hint}>{M.sourceModeStep}</p>
      <label style={fieldStyles.label}>
        {M.planTitleLabel}
        <input value={title} style={fieldStyles.input} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label style={fieldStyles.label}>
        {M.sourceModeLabel}
        <select value={mode} style={fieldStyles.input} onChange={(event) => setMode(event.target.value as SourceMode)}>
          {Object.entries(SOURCE_MODE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {failed && <p role="alert">{M.createFailed}</p>}
      <button type="button" disabled={busy} onClick={() => void create()}>
        {M.createPlan}
      </button>
    </section>
  );
}

type Loaded = { kind: "loading" } | { kind: "error" } | { kind: "ready"; plan: Plan; source: Source };

/** Step 2: the wizard for the plan in the URL; on acceptance it opens the editor. */
export function PlanCalibrationPage() {
  const { caseId = "", planId = "" } = useParams();
  const navigate = useNavigate();
  const [state, setState] = useState<Loaded>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    async function load(): Promise<void> {
      try {
        const plan = await api.get("/api/v1/plans/{plan_id}", { path: { plan_id: planId } });
        const source = await api.get("/api/v1/sources/{source_id}", { path: { source_id: plan.primary_source_id } });
        if (active) setState({ kind: "ready", plan, source });
      } catch {
        if (active) setState({ kind: "error" });
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [planId]);

  if (state.kind === "loading") return <p role="status">{M.loading}</p>;
  if (state.kind === "error") return <p role="alert">{M.loadFailed}</p>;
  return (
    <CalibrationWizard
      plan={state.plan}
      source={state.source}
      onAccepted={(plan) => void navigate(planEditorPath(caseId, plan.id))}
    />
  );
}
