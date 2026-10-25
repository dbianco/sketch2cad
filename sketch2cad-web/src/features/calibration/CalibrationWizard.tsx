import { useState, type CSSProperties } from "react";
import { ApiError, api } from "../../api/client";
import type { components } from "../../api/schema";
import { SourceViewer, type MarkedPoint } from "../sources/SourceViewer";
import { FourPointStep, type CheckPointDraft } from "./FourPointStep";
import {
  CALIBRATION_MESSAGES as M,
  METHOD_LABELS,
  SOURCE_MODE_LABELS,
  formatMm,
  v05Message,
  v12Message,
  type CalibrationMethod,
} from "./messages";
import { DecimalField, DpiStep, SegmentStep, fieldStyles, parseDecimal } from "./ScanSteps";

export type Plan = components["schemas"]["PlanOut"];
type Source = components["schemas"]["SourceOut"];
type CalibrationIn = components["schemas"]["CalibrationIn"];
type Proposal = components["schemas"]["CalibrationProposalOut"];
type Point = [number, number];

/** Control points each method needs. */
const NEEDED_POINTS: Readonly<Record<CalibrationMethod, number>> = {
  photo_homography_4pt: 4,
  scan_segment: 2,
  scan_dpi: 0,
  none: 0,
};

interface Inputs {
  method: CalibrationMethod;
  points: Point[];
  checks: CheckPointDraft[];
  segmentMm: string;
  width: string;
  height: string;
  paperScale: string;
}

const positive = (text: string) => {
  const value = parseDecimal(text);
  return value !== null && value > 0 ? value : null;
};

/** The request body of the inputs, or the reason why they do not define a calibration yet. */
export function buildCalibration(inputs: Inputs, plan: Plan): CalibrationIn | string {
  const scale = Number(inputs.paperScale.trim());
  const paperScale =
    plan.source_mode === "scaled_drawing" && Number.isInteger(scale) && scale > 0 ? scale : null;
  const control_points = inputs.points.map((image_px) => ({ image_px }));
  switch (inputs.method) {
    case "none":
      return { method: "none" };
    case "scan_dpi":
      return { method: "scan_dpi", paper_scale: paperScale };
    case "scan_segment": {
      const segment = positive(inputs.segmentMm);
      if (inputs.points.length < 2 || segment === null) return M.noMetricSegment;
      return { method: "scan_segment", control_points, segment_mm: segment, paper_scale: paperScale };
    }
    case "photo_homography_4pt": {
      if (inputs.points.length < 4) return M.noMetricPoints;
      const width = positive(inputs.width);
      const height = positive(inputs.height);
      if (width === null || height === null) return M.noMetricDimensions;
      const check_points = inputs.checks.flatMap((check) => {
        const x = parseDecimal(check.x);
        const y = parseDecimal(check.y);
        return x === null || y === null ? [] : [{ image_px: check.px, paper_mm: [x, y] as Point }];
      });
      return {
        method: "photo_homography_4pt",
        control_points,
        check_points,
        known_dimensions_mm: { width, height },
        paper_scale: paperScale,
      };
    }
  }
}

function problemCode(error: ApiError): string | undefined {
  return typeof error.problem.code === "string" ? error.problem.code : undefined;
}

/** UI message of a failed request: rule messages (V-xx) verbatim, a generic one otherwise. */
function failureMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (problemCode(error) === "V-12") {
    const holder = error.problem.holder as { display_name?: unknown } | undefined;
    if (typeof holder?.display_name === "string") return v12Message(holder.display_name);
  }
  const code = problemCode(error);
  return code?.startsWith("V-") && error.detail !== undefined ? error.detail : fallback;
}

const decimals = new Intl.NumberFormat("es", { maximumFractionDigits: 4 });

const styles = {
  page: { display: "flex", minHeight: "100%", background: "#F4F6F8", color: "#14202B" },
  stage: { flex: "1 1 0", padding: 24, display: "flex", flexDirection: "column", gap: 12 },
  panel: {
    width: 340,
    background: "#fff",
    borderLeft: "1px solid #D5DCE3",
    padding: 24,
    display: "flex",
    flexDirection: "column",
    gap: 16,
  },
  summary: { background: "#F4F6F8", borderRadius: 8, padding: 14, fontSize: 14, lineHeight: 1.6 },
  ok: { color: "#145A32", fontWeight: 600 },
  alert: { margin: 0, padding: "8px 10px", color: "#8A1C1C", background: "#FDECEC", borderRadius: 4, fontSize: 14 },
  actions: { display: "flex", flexWrap: "wrap", gap: 10, marginTop: "auto" },
  matrix: { fontFamily: "ui-monospace,Menlo,monospace", fontSize: 12 },
} satisfies Record<string, CSSProperties>;

function ProposalSummary({ proposal }: { proposal: Proposal }) {
  const error = proposal.reprojection_error_mm;
  return (
    <div aria-label="Propuesta de calibración" style={styles.summary}>
      {proposal.scale !== null && <div>Escala: {decimals.format(proposal.scale.mm_per_px)} mm/px</div>}
      {proposal.image_to_paper !== null && (
        <div style={styles.matrix} aria-label="Matriz imagen a papel">
          {proposal.image_to_paper.map((row, index) => (
            <div key={index}>[{row.map((value) => decimals.format(value)).join("; ")}]</div>
          ))}
        </div>
      )}
      {error !== null && (
        <>
          <div>
            Error de reproyección máximo: <b>{formatMm(error.max)}</b>
          </div>
          <div>
            Error RMS: <b>{formatMm(error.rms)}</b>
          </div>
          <div>{proposal.reprojection_error_basis === "check" ? M.basisCheck : M.basisControl}</div>
        </>
      )}
      {proposal.meets_tolerance && proposal.method !== "none" && (
        <div style={styles.ok}>{M.withinTolerance(proposal.max_reprojection_error_mm)}</div>
      )}
    </div>
  );
}

export interface CalibrationWizardProps {
  plan: Plan;
  source: Source;
  onAccepted: (plan: Plan) => void;
}

const EMPTY: Omit<Inputs, "method" | "paperScale"> = { points: [], checks: [], segmentMm: "", width: "", height: "" };

/**
 * Calibration of a plan (prototype screen 3, US-03, V-05): method, points marked on the source
 * in original pixels, a proposal computed by `PUT /plans/{id}/calibration`, and acceptance as
 * metric or as approximate after taking the edit lease.
 */
export function CalibrationWizard({ plan, source, onAccepted }: CalibrationWizardProps) {
  const [inputs, setInputs] = useState<Inputs>({ ...EMPTY, method: "photo_homography_4pt", paperScale: "" });
  const [markingCheck, setMarkingCheck] = useState(false);
  const [proposal, setProposal] = useState<{ key: string; value: Proposal } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const body = buildCalibration(inputs, plan);
  const complete = typeof body !== "string";
  const bodyKey = complete ? JSON.stringify(body) : "";
  const current = proposal !== null && proposal.key === bodyKey ? proposal.value : null;
  const update = (patch: Partial<Inputs>) => setInputs((previous) => ({ ...previous, ...patch }));

  let metricBlocked: string | null = null;
  let v05 = false;
  if (inputs.method === "none") metricBlocked = M.noMetricNone;
  else if (!complete) metricBlocked = body;
  else if (plan.source_mode === "scaled_drawing" && body.paper_scale == null) metricBlocked = M.noMetricPaperScale;
  else if (current === null) metricBlocked = M.noMetricProposal;
  else if (!current.meets_tolerance && current.reprojection_error_mm !== null) {
    metricBlocked = v05Message(current.reprojection_error_mm.max, current.max_reprojection_error_mm);
    v05 = true;
  }

  function handlePoint(px: Point): void {
    if (markingCheck) {
      setMarkingCheck(false);
      update({ checks: [...inputs.checks, { px, x: "", y: "" }] });
    } else if (inputs.points.length < NEEDED_POINTS[inputs.method]) {
      update({ points: [...inputs.points, px] });
    }
  }

  async function propose(): Promise<void> {
    if (!complete) return;
    setBusy(true);
    setFailure(null);
    try {
      const value = await api.put("/api/v1/plans/{plan_id}/calibration", { path: { plan_id: plan.id }, body });
      setProposal({ key: bodyKey, value });
    } catch (error) {
      setFailure(failureMessage(error, M.proposeFailed));
    } finally {
      setBusy(false);
    }
  }

  async function accept(asMetric: boolean): Promise<void> {
    if (!complete) return;
    setBusy(true);
    setFailure(null);
    try {
      await api.post("/api/v1/plans/{plan_id}/lease", { path: { plan_id: plan.id } });
      const accepted = await api.post("/api/v1/plans/{plan_id}/calibration/accept", {
        path: { plan_id: plan.id },
        body: { ...body, as_metric: asMetric },
      });
      onAccepted(accepted);
    } catch (error) {
      setFailure(failureMessage(error, M.acceptFailed));
      setBusy(false);
    }
  }

  const marked: MarkedPoint[] = [
    ...inputs.points.map((px, index) => ({ px, label: String(index + 1) })),
    ...inputs.checks.map((check, index) => ({ px: check.px, label: `C${String(index + 1)}`, color: "#0B6E8A" })),
  ];
  const needsClicks = markingCheck || inputs.points.length < NEEDED_POINTS[inputs.method];

  return (
    <div style={styles.page}>
      <div style={styles.stage}>
        <p style={fieldStyles.hint}>{needsClicks ? M.clickToMark : M.title}</p>
        <SourceViewer
          sourceId={source.id}
          alt={source.original_filename}
          marking={
            source.width_px === null || source.height_px === null
              ? undefined
              : { imageSize: { width: source.width_px, height: source.height_px }, points: marked, onPoint: handlePoint }
          }
        />
      </div>
      <section aria-label={M.title} style={styles.panel}>
        <h2 style={{ margin: 0, fontSize: 20 }}>{M.title}</h2>
        <p style={fieldStyles.hint}>
          {M.methodStep} · {M.sourceModeLabel}: {SOURCE_MODE_LABELS[plan.source_mode]}
        </p>
        <label style={fieldStyles.label}>
          {M.methodLabel}
          <select
            value={inputs.method}
            style={fieldStyles.input}
            onChange={(event) => {
              setMarkingCheck(false);
              update({ ...EMPTY, method: event.target.value as CalibrationMethod });
            }}
          >
            {Object.entries(METHOD_LABELS).map(([method, label]) => (
              <option key={method} value={method}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {plan.source_mode === "scaled_drawing" && inputs.method !== "none" && (
          <DecimalField label={M.paperScaleLabel} value={inputs.paperScale} onChange={(paperScale) => update({ paperScale })} />
        )}
        {inputs.method === "scan_dpi" && <DpiStep dpi={source.dpi} />}
        {inputs.method === "scan_segment" && (
          <SegmentStep
            marked={inputs.points.length}
            segmentMm={inputs.segmentMm}
            onSegmentMm={(segmentMm) => update({ segmentMm })}
            scale={current?.scale ?? null}
          />
        )}
        {inputs.method === "photo_homography_4pt" && (
          <FourPointStep
            marked={inputs.points.length}
            width={inputs.width}
            height={inputs.height}
            onWidth={(width) => update({ width })}
            onHeight={(height) => update({ height })}
            checks={inputs.checks}
            onCheckChange={(index, field, value) =>
              update({ checks: inputs.checks.map((check, i) => (i === index ? { ...check, [field]: value } : check)) })
            }
            onRemoveCheck={(index) => update({ checks: inputs.checks.filter((_check, i) => i !== index) })}
            markingCheck={markingCheck}
            onAddCheck={() => setMarkingCheck(true)}
          />
        )}
        {inputs.method === "none" && <p style={fieldStyles.hint}>{M.noneHint}</p>}
        {current !== null && <ProposalSummary proposal={current} />}
        {proposal !== null && current === null && complete && <p style={fieldStyles.hint}>{M.outdated}</p>}
        {metricBlocked !== null && (
          <p id="metric-blocked" role={v05 ? "alert" : undefined} style={v05 ? styles.alert : fieldStyles.hint}>
            {metricBlocked}
          </p>
        )}
        {failure !== null && (
          <p role="alert" style={styles.alert}>
            {failure}
          </p>
        )}
        <div style={styles.actions}>
          {NEEDED_POINTS[inputs.method] > 0 && (
            <button type="button" disabled={busy} onClick={() => update({ points: [], checks: [] })}>
              {M.resetPoints}
            </button>
          )}
          {inputs.method !== "none" && (
            <button type="button" disabled={busy || !complete} onClick={() => void propose()}>
              {M.propose}
            </button>
          )}
          <button
            type="button"
            disabled={busy || metricBlocked !== null}
            aria-describedby={metricBlocked !== null ? "metric-blocked" : undefined}
            onClick={() => void accept(true)}
          >
            {M.acceptMetric}
          </button>
          <button type="button" disabled={busy || !complete} onClick={() => void accept(false)}>
            {M.acceptApproximate}
          </button>
        </div>
      </section>
    </div>
  );
}
