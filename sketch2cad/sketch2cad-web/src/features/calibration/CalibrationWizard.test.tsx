import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { components } from "../../api/schema";
import { previewUrl } from "../sources/SourceViewer";
import { PlanCalibrationPage, SourceCalibrationPage } from "./CalibrationPage";
import { CalibrationWizard, type Plan } from "./CalibrationWizard";
import { CALIBRATION_MESSAGES as M, v12Message } from "./messages";

type Proposal = components["schemas"]["CalibrationProposalOut"];
type Source = components["schemas"]["SourceOut"];

const CASE_ID = "00000000-0000-4000-8000-0000000000c1";
const SOURCE_ID = "00000000-0000-4000-8000-0000000000s1";
const PLAN_ID = "00000000-0000-4000-8000-0000000000p1";
const PLAN_URL = `/api/v1/plans/${PLAN_ID}`;
const PROPOSE_URL = `${PLAN_URL}/calibration`;
const ACCEPT_URL = `${PLAN_URL}/calibration/accept`;
const LEASE_URL = `${PLAN_URL}/lease`;
const CREATE_URL = `/api/v1/case-files/${CASE_ID}/plans`;
const SOURCE_URL = `/api/v1/sources/${SOURCE_ID}`;
// The rendered preview box: 1000 x 750 screen px for a 4032 x 3024 px original.
const RECT = { left: 10, top: 20, width: 1000, height: 750 };

const PLAN: Plan = {
  id: PLAN_ID,
  case_file_id: CASE_ID,
  title: "Plano 1",
  primary_source_id: SOURCE_ID,
  source_mode: "schematic_sketch",
  status: "CALIBRATION_PENDING",
  metric_status: "non_metric",
  calibration: null,
  lease: null,
  current_revision: null,
  current_revision_id: null,
  validated_revision_id: null,
  created_at: "2026-10-25T10:00:00Z",
};

const SOURCE: Source = {
  id: SOURCE_ID,
  case_file_id: CASE_ID,
  original_filename: "croquis_cruce_01.jpg",
  media_type: "image/jpeg",
  byte_size: 17,
  sha256: "0".repeat(64),
  width_px: 4032,
  height_px: 3024,
  dpi: 300,
  exif: {},
  qc: null,
  qc_job_id: "00000000-0000-4000-8000-0000000000j1",
  uploaded_by: "8a7c0d4e-0000-4000-8000-000000000001",
  uploaded_at: "2026-10-25T10:00:00Z",
};

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    method: "photo_homography_4pt",
    image_to_paper: [
      [0.05, 0, -5],
      [0, 0.05, -1],
      [0, 0, 1],
    ],
    control_points: [],
    reprojection_error_mm: { max: 0.6, rms: 0.3 },
    reprojection_error_basis: "check",
    meets_tolerance: true,
    max_reprojection_error_mm: 1.0,
    paper_scale: null,
    scale: null,
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();
type Handler = () => Response;

/** Serve `routes` keyed by `METHOD url`; the preview is a JPEG and anything else a 404. */
function mockApi(routes: Record<string, Handler>): void {
  fetchMock.mockImplementation((input, init) => {
    const url = String(input);
    if (url === previewUrl(SOURCE_ID)) {
      return Promise.resolve(new Response(new Blob([new Uint8Array([0xff, 0xd8])]), { status: 200 }));
    }
    const handler = routes[`${init?.method ?? "GET"} ${url}`];
    return Promise.resolve(handler === undefined ? json({ status: 404 }, 404) : handler());
  });
}

function calls(method: string, url: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([input, init]) => String(input) === url && init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body ?? "null")) as unknown);
}

const onAccepted = vi.fn<(plan: Plan) => void>();

async function renderWizard(plan: Plan = PLAN): Promise<HTMLElement> {
  render(<CalibrationWizard plan={plan} source={SOURCE} onAccepted={onAccepted} />);
  const image = await screen.findByRole("img", { name: SOURCE.original_filename });
  vi.spyOn(image, "getBoundingClientRect").mockReturnValue({ ...RECT, x: 0, y: 0 } as DOMRect);
  return image;
}

/** Click the image at a fraction of the rendered box. */
function clickAt(image: HTMLElement, fx: number, fy: number): void {
  const clientX = RECT.left + fx * RECT.width;
  const clientY = RECT.top + fy * RECT.height;
  fireEvent.mouseDown(image, { clientX, clientY });
  fireEvent.click(image, { clientX, clientY });
}

const CORNERS: [number, number][] = [
  [0.1, 0.1],
  [0.9, 0.15],
  [0.85, 0.9],
  [0.05, 0.8],
];

function type(label: string, value: string): void {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function markFourPoints(): Promise<void> {
  const image = await renderWizard();
  for (const [fx, fy] of CORNERS) clickAt(image, fx, fy);
  type(M.widthLabel, "210,0");
  type(M.heightLabel, "297");
}

const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

beforeEach(() => {
  fetchMock.mockReset();
  onAccepted.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  Object.assign(URL, { createObjectURL: () => "blob:preview", revokeObjectURL: () => undefined });
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CalibrationWizard", () => {
  it("blocks accept-as-metric with the V-05 message when the error exceeds the tolerance", async () => {
    const tooLarge = proposal({ reprojection_error_mm: { max: 1.3, rms: 0.9 }, meets_tolerance: false });
    mockApi({ [`PUT ${PROPOSE_URL}`]: () => json(tooLarge) });
    await markFourPoints();

    fireEvent.click(button(M.propose));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "El error de calibración es de 1,3 mm y supera el máximo de 1,0 mm. Repite los puntos o continúa como aproximado.",
    );
    expect(alert.textContent).toContain("1,3 mm");
    expect(alert.textContent).toContain("1,0 mm");
    expect(button(M.acceptMetric).disabled).toBe(true);
    expect(button(M.acceptApproximate).disabled).toBe(false);
  });

  it("sends four control points in original image pixels in the PUT body", async () => {
    mockApi({ [`PUT ${PROPOSE_URL}`]: () => json(proposal()) });
    await markFourPoints();
    expect(screen.getAllByTestId("source-marker")).toHaveLength(4);

    fireEvent.click(button(M.propose));

    await screen.findByLabelText("Propuesta de calibración");
    expect(calls("PUT", PROPOSE_URL)).toEqual([
      {
        method: "photo_homography_4pt",
        control_points: [
          { image_px: [403.2, 302.4] },
          { image_px: [3628.8, 453.6] },
          { image_px: [3427.2, 2721.6] },
          { image_px: [201.6, 2419.2] },
        ],
        check_points: [],
        known_dimensions_mm: { width: 210, height: 297 },
        paper_scale: null,
      },
    ]);
    expect(button(M.acceptMetric).disabled).toBe(false);
  });

  it("ignores a press that pans the image and a fifth corner", async () => {
    mockApi({});
    const image = await renderWizard();
    fireEvent.mouseDown(image, { clientX: 100, clientY: 100 });
    fireEvent.click(image, { clientX: 300, clientY: 100 });
    expect(screen.queryAllByTestId("source-marker")).toHaveLength(0);

    for (const [fx, fy] of [...CORNERS, [0.5, 0.5] as [number, number]]) clickAt(image, fx, fy);
    expect(screen.getAllByTestId("source-marker")).toHaveLength(4);
  });

  it("does not let a photo be marked metric before the four points are complete", async () => {
    mockApi({});
    const image = await renderWizard();
    for (const [fx, fy] of CORNERS.slice(0, 3)) clickAt(image, fx, fy);
    type(M.widthLabel, "210");
    type(M.heightLabel, "297");

    expect(button(M.acceptMetric).disabled).toBe(true);
    expect(button(M.acceptApproximate).disabled).toBe(true);
    expect(button(M.propose).disabled).toBe(true);
    expect(screen.getByText(M.noMetricPoints)).toBeTruthy();
    fireEvent.click(button(M.acceptMetric));
    expect(calls("POST", LEASE_URL)).toHaveLength(0);
    expect(calls("POST", ACCEPT_URL)).toHaveLength(0);
  });

  it("measures the error on a check point with its pattern position", async () => {
    mockApi({ [`PUT ${PROPOSE_URL}`]: () => json(proposal()) });
    await markFourPoints();
    fireEvent.click(button(M.addCheck));
    clickAt(screen.getByRole("img"), 0.5, 0.5);
    type(M.checkX, "105");
    type(M.checkY, "148,5");

    fireEvent.click(button(M.propose));

    await screen.findByText(M.basisCheck);
    const [body] = calls("PUT", PROPOSE_URL) as { check_points: unknown }[];
    expect(body?.check_points).toEqual([{ image_px: [2016, 1512], paper_mm: [105, 148.5] }]);
  });

  it("shows the lease holder and does not accept when another user edits the plan", async () => {
    mockApi({
      [`PUT ${PROPOSE_URL}`]: () => json(proposal()),
      [`POST ${LEASE_URL}`]: () =>
        json({ status: 409, code: "V-12", detail: "x", holder: { display_name: "Ana Pérez" } }, 409),
    });
    await markFourPoints();
    fireEvent.click(button(M.propose));
    await screen.findByLabelText("Propuesta de calibración");

    fireEvent.click(button(M.acceptMetric));

    expect((await screen.findByRole("alert")).textContent).toBe(v12Message("Ana Pérez"));
    expect(calls("POST", ACCEPT_URL)).toHaveLength(0);
    expect(onAccepted).not.toHaveBeenCalled();
  });

  it("calibrates a scan by DPI and accepts it as metric after taking the lease", async () => {
    const accepted = { ...PLAN, status: "EDITING" as const, metric_status: "approximate" as const };
    mockApi({
      [`PUT ${PROPOSE_URL}`]: () =>
        json(proposal({ method: "scan_dpi", reprojection_error_mm: null, reprojection_error_basis: null })),
      [`POST ${LEASE_URL}`]: () => json({ user_id: "u", display_name: "Yo", expires_at: "2026-10-25T10:10:00Z" }),
      [`POST ${ACCEPT_URL}`]: () => json(accepted),
    });
    await renderWizard();
    fireEvent.change(screen.getByLabelText(M.methodLabel), { target: { value: "scan_dpi" } });
    expect(screen.getByText(/DPI del archivo: 300/)).toBeTruthy();

    fireEvent.click(button(M.propose));
    await screen.findByLabelText("Propuesta de calibración");
    fireEvent.click(button(M.acceptMetric));

    await waitFor(() => expect(onAccepted).toHaveBeenCalledWith(accepted));
    expect(calls("PUT", PROPOSE_URL)).toEqual([{ method: "scan_dpi", paper_scale: null }]);
    expect(calls("POST", ACCEPT_URL)).toEqual([{ method: "scan_dpi", paper_scale: null, as_metric: true }]);
    const order = fetchMock.mock.calls.map(([input]) => String(input));
    expect(order.indexOf(LEASE_URL)).toBeLessThan(order.indexOf(ACCEPT_URL));
  });

  it("continues without calibration as approximate only", async () => {
    mockApi({
      [`POST ${LEASE_URL}`]: () => json({ user_id: "u", display_name: "Yo", expires_at: "2026-10-25T10:10:00Z" }),
      [`POST ${ACCEPT_URL}`]: () => json(PLAN),
    });
    await renderWizard();
    fireEvent.change(screen.getByLabelText(M.methodLabel), { target: { value: "none" } });

    expect(button(M.acceptMetric).disabled).toBe(true);
    expect(screen.getByText(M.noMetricNone)).toBeTruthy();
    fireEvent.click(button(M.acceptApproximate));

    await waitFor(() => expect(onAccepted).toHaveBeenCalled());
    expect(calls("POST", ACCEPT_URL)).toEqual([{ method: "none", as_metric: false }]);
  });

  it("requires the paper scale of a scaled drawing to accept it as metric", async () => {
    mockApi({ [`PUT ${PROPOSE_URL}`]: () => json(proposal({ method: "scan_dpi", reprojection_error_mm: null })) });
    await renderWizard({ ...PLAN, source_mode: "scaled_drawing" });
    fireEvent.change(screen.getByLabelText(M.methodLabel), { target: { value: "scan_dpi" } });
    expect(screen.getByText(M.noMetricPaperScale)).toBeTruthy();

    type(M.paperScaleLabel, "100");
    fireEvent.click(button(M.propose));

    await waitFor(() => expect(button(M.acceptMetric).disabled).toBe(false));
    expect(calls("PUT", PROPOSE_URL)).toEqual([{ method: "scan_dpi", paper_scale: 100 }]);
  });
});

function LocationProbe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

describe("calibration routes", () => {
  function renderAt(path: string): void {
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/cases/:caseId/sources/:sourceId/calibration" element={<SourceCalibrationPage />} />
          <Route path="/cases/:caseId/plans/:planId/calibration" element={<PlanCalibrationPage />} />
          <Route path="*" element={null} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>,
    );
  }

  it("creates the plan as a schematic sketch by default and moves to its calibration URL", async () => {
    mockApi({
      [`POST ${CREATE_URL}`]: () => json(PLAN, 201),
      [`GET ${PLAN_URL}`]: () => json(PLAN),
      [`GET ${SOURCE_URL}`]: () => json(SOURCE),
    });
    renderAt(`/cases/${CASE_ID}/sources/${SOURCE_ID}/calibration`);
    expect((screen.getByLabelText(M.sourceModeLabel) as HTMLSelectElement).value).toBe("schematic_sketch");

    fireEvent.click(button(M.createPlan));

    await screen.findByRole("img", { name: SOURCE.original_filename });
    expect(calls("POST", CREATE_URL)).toEqual([
      { title: "Plano 1", primary_source_id: SOURCE_ID, source_mode: "schematic_sketch" },
    ]);
    expect(screen.getByLabelText("location").textContent).toBe(`/cases/${CASE_ID}/plans/${PLAN_ID}/calibration`);
  });

  it("reloads the wizard from the plan id in the URL", async () => {
    mockApi({ [`GET ${PLAN_URL}`]: () => json(PLAN), [`GET ${SOURCE_URL}`]: () => json(SOURCE) });
    renderAt(`/cases/${CASE_ID}/plans/${PLAN_ID}/calibration`);

    await screen.findByRole("img", { name: SOURCE.original_filename });
    expect(screen.getByText(/Croquis esquemático/)).toBeTruthy();
    expect(calls("POST", CREATE_URL)).toHaveLength(0);
  });
});
