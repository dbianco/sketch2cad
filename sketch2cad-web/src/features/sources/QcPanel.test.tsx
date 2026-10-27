import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { QcPanel, STATUS_GUIDANCE, parseQc } from "./QcPanel";

// `source.qc` as the API stores it since checks carry cause/action.
const RED_API_QC = {
  status: "red",
  checks: [
    {
      name: "short_side",
      status: "red",
      value: 600,
      threshold: 900,
      message: "Resolución insuficiente: el lado corto mide 600 px (mínimo 900 px).",
      cause: "Resolución insuficiente: el lado corto mide 600 px (mínimo 900 px).",
      action: "Vuelve a fotografiar o escanear el croquis con al menos 900 px en el lado corto.",
    },
    {
      name: "laplacian_variance",
      status: "amber",
      value: 62,
      threshold: 100,
      message: "Imagen algo borrosa: algunos trazos o cotas pueden no leerse.",
      cause: "Imagen algo borrosa: la varianza del Laplaciano es 62,0.",
      action: "Si puedes, vuelve a fotografiarlo enfocado; si no, revisa las cotas a mano.",
    },
    {
      name: "contrast",
      status: "green",
      value: 80,
      threshold: 60,
      message: "Contraste adecuado.",
      cause: null,
      action: null,
    },
  ],
};

function reasons(): string[] {
  const list = screen.getByRole("list", { name: "Motivos" });
  return within(list)
    .getAllByRole("listitem")
    .map((item) => item.textContent ?? "");
}

describe("QcPanel", () => {
  afterEach(cleanup);

  it("parses the cause and action of each check", () => {
    const qc = parseQc(RED_API_QC);
    expect(qc?.checks.map((check) => [check.cause, check.action])).toEqual([
      [RED_API_QC.checks[0]?.cause, RED_API_QC.checks[0]?.action],
      [RED_API_QC.checks[1]?.cause, RED_API_QC.checks[1]?.action],
      [null, null],
    ]);
  });

  it("shows the API's cause and action of every non-green check", () => {
    const qc = parseQc(RED_API_QC);
    if (qc === null) throw new Error("unparsed");
    render(<QcPanel qc={qc} />);

    expect(reasons()).toEqual([
      "Resolución insuficiente: el lado corto mide 600 px (mínimo 900 px). Qué hacer: Vuelve a " +
        "fotografiar o escanear el croquis con al menos 900 px en el lado corto.",
      "Imagen algo borrosa: la varianza del Laplaciano es 62,0. Qué hacer: Si puedes, vuelve a " +
        "fotografiarlo enfocado; si no, revisa las cotas a mano.",
    ]);
  });

  it("says that suggestions may be unreliable on red, not that they will not run", () => {
    const qc = parseQc(RED_API_QC);
    if (qc === null) throw new Error("unparsed");
    render(<QcPanel qc={qc} />);

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(STATUS_GUIDANCE.red);
    expect(STATUS_GUIDANCE.red).toContain("pueden ser poco fiables");
    expect(alert.textContent).not.toContain("insuficiente para las sugerencias");
    expect(alert.textContent).not.toContain("modo manual");
  });

  it("falls back to the message when a stored result has no cause or action", () => {
    const qc = parseQc({
      status: "amber",
      checks: [{ name: "contrast", status: "amber", value: 40, threshold: 60, message: "Contraste bajo." }],
    });
    if (qc === null) throw new Error("unparsed");
    render(<QcPanel qc={qc} />);

    expect(reasons()).toEqual(["Contraste bajo."]);
  });
});
