import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CaseListPage } from "./CaseListPage";
import { localToday, type CaseFile } from "./NewCaseForm";

const LIST_URL = "/api/v1/case-files";

function caseFile(overrides: Partial<CaseFile>): CaseFile {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    code: "EXP-2026-0001",
    incident_type: "crime_scene",
    incident_date: "2026-10-02",
    description: "Robo en local",
    declared_location: "Local comercial",
    organization: null,
    status: "open",
    created_by: "8a7c0d4e-0000-4000-8000-000000000001",
    created_at: "2026-10-02T10:00:00Z",
    ...overrides,
  };
}

const EXISTING = [
  caseFile({
    id: "00000000-0000-4000-8000-000000000002",
    code: "EXP-2026-0002",
    incident_type: "inspection",
    incident_date: "2026-10-14",
    declared_location: "Vivienda, planta baja",
  }),
  caseFile({}),
];

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function calls(method: string): { url: string; init: RequestInit | undefined }[] {
  return fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? "GET") === method)
    .map(([input, init]) => ({ url: String(input), init }));
}

function mockList(items: CaseFile[] = EXISTING): void {
  fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ items })));
}

function tableRows(): HTMLElement[] {
  const table = screen.getByRole("table", { name: "Lista de expedientes" });
  return within(table).getAllByRole("row").slice(1);
}

describe("CaseListPage", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    document.cookie = "s2c_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("lists the cases returned by the API", async () => {
    mockList();
    render(<CaseListPage />);

    expect(screen.getByText("Cargando expedientes…")).toBeTruthy();
    await screen.findByRole("table", { name: "Lista de expedientes" });

    const rows = tableRows();
    expect(rows).toHaveLength(2);
    const cells = within(rows[0] as HTMLElement)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(within(rows[0] as HTMLElement).getByRole("rowheader").textContent).toBe("EXP-2026-0002");
    expect(cells).toEqual(["Inspección ocular", "2026-10-14", "Vivienda, planta baja", "Abierto"]);
    expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Código",
      "Tipo de hecho",
      "Fecha",
      "Ubicación declarada",
      "Estado",
    ]);
    expect(calls("GET").map((call) => call.url)).toEqual([LIST_URL]);
  });

  it("shows the empty state when there are no cases", async () => {
    mockList([]);
    render(<CaseListPage />);

    expect(await screen.findByText("Aún no tienes expedientes.")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows an alert when the list cannot be loaded and retries on demand", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ title: "Internal Server Error" }, 500));
    render(<CaseListPage />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No se pudieron cargar los expedientes.");

    mockList();
    fireEvent.click(within(alert).getByRole("button", { name: "Reintentar" }));
    await screen.findByRole("table", { name: "Lista de expedientes" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sends the incident type filter and the debounced search as query parameters", async () => {
    mockList();
    render(<CaseListPage />);
    await screen.findByRole("table");

    fireEvent.change(screen.getByLabelText("Tipo de hecho"), { target: { value: "inspection" } });
    await waitFor(() => {
      expect(calls("GET").at(-1)?.url).toBe(`${LIST_URL}?incident_type=inspection`);
    });

    fireEvent.change(screen.getByLabelText("Buscar"), { target: { value: " EXP-2026 " } });
    await waitFor(() => {
      expect(calls("GET").at(-1)?.url).toBe(`${LIST_URL}?q=EXP-2026&incident_type=inspection`);
    });
    expect(calls("GET")).toHaveLength(3);
  });

  it("shows a filtered empty message when nothing matches", async () => {
    mockList([]);
    render(<CaseListPage />);
    await screen.findByText("Aún no tienes expedientes.");

    fireEvent.change(screen.getByLabelText("Tipo de hecho"), { target: { value: "other" } });
    expect(await screen.findByText("Ningún expediente coincide con la búsqueda.")).toBeTruthy();
  });

  it("creates a case and adds its row at the top with the returned code", async () => {
    document.cookie = "s2c_csrf=csrf-token-123; path=/";
    const created = caseFile({
      id: "00000000-0000-4000-8000-000000000003",
      code: "EXP-2026-0003",
      incident_type: "traffic_collision",
      incident_date: "2026-10-21",
      declared_location: "Av. Principal y Calle 4",
    });
    fetchMock.mockImplementation((_input, init) =>
      Promise.resolve(
        init?.method === "POST" ? jsonResponse(created, 201) : jsonResponse({ items: EXISTING }),
      ),
    );
    render(<CaseListPage />);
    await screen.findByRole("table");

    const opener = screen.getByRole("button", { name: "Nuevo expediente" });
    fireEvent.click(opener);
    const form = screen.getByRole("form", { name: "Nuevo expediente" });
    const typeSelect = within(form).getByLabelText("Tipo de hecho");
    expect(document.activeElement).toBe(typeSelect);
    expect((within(form).getByLabelText("Fecha del hecho") as HTMLInputElement).value).toBe(
      localToday(),
    );

    fireEvent.change(within(form).getByLabelText("Fecha del hecho"), {
      target: { value: "2026-10-21" },
    });
    fireEvent.change(within(form).getByLabelText("Descripción"), {
      target: { value: "Colisión entre dos vehículos" },
    });
    fireEvent.change(within(form).getByLabelText("Ubicación declarada"), {
      target: { value: "Av. Principal y Calle 4" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Crear expediente" }));

    await waitFor(() => {
      expect(screen.queryByRole("form", { name: "Nuevo expediente" })).toBeNull();
    });
    const rows = tableRows();
    expect(rows).toHaveLength(3);
    expect(within(rows[0] as HTMLElement).getByRole("rowheader").textContent).toBe("EXP-2026-0003");
    expect(screen.getByText("Expediente EXP-2026-0003 creado.")).toBeTruthy();
    expect(document.activeElement).toBe(opener);

    const [post] = calls("POST");
    expect(calls("POST")).toHaveLength(1);
    expect(post?.url).toBe(LIST_URL);
    expect(new Headers(post?.init?.headers).get("X-CSRF-Token")).toBe("csrf-token-123");
    expect(JSON.parse(String(post?.init?.body))).toEqual({
      incident_type: "traffic_collision",
      incident_date: "2026-10-21",
      description: "Colisión entre dos vehículos",
      declared_location: "Av. Principal y Calle 4",
    });
  });

  it("validates required fields before sending any request", async () => {
    mockList();
    render(<CaseListPage />);
    await screen.findByRole("table");
    const requestsBefore = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Nuevo expediente" }));
    const form = screen.getByRole("form", { name: "Nuevo expediente" });
    fireEvent.click(within(form).getByRole("button", { name: "Crear expediente" }));

    expect(within(form).getByText("Ingresa una descripción")).toBeTruthy();
    expect(within(form).getByText("Ingresa la ubicación declarada")).toBeTruthy();
    const description = within(form).getByLabelText("Descripción");
    expect(description.getAttribute("aria-invalid")).toBe("true");
    expect(description.getAttribute("aria-describedby")).toBe("new-case-description-error");
    expect(document.getElementById("new-case-description-error")?.textContent).toBe(
      "Ingresa una descripción",
    );
    expect(document.activeElement).toBe(description);
    expect(fetchMock.mock.calls.length).toBe(requestsBefore);
    expect(calls("POST")).toHaveLength(0);
  });

  it("maps server validation errors to their fields", async () => {
    fetchMock.mockImplementation((_input, init) =>
      Promise.resolve(
        init?.method === "POST"
          ? jsonResponse(
              {
                title: "Unprocessable Entity",
                status: 422,
                errors: [{ loc: ["body", "declared_location"], msg: "Field required", type: "missing" }],
              },
              422,
            )
          : jsonResponse({ items: EXISTING }),
      ),
    );
    render(<CaseListPage />);
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "Nuevo expediente" }));
    const form = screen.getByRole("form", { name: "Nuevo expediente" });
    fireEvent.change(within(form).getByLabelText("Descripción"), { target: { value: "Hecho" } });
    fireEvent.change(within(form).getByLabelText("Ubicación declarada"), {
      target: { value: "Calle 1" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Crear expediente" }));

    expect(await within(form).findByText("Ingresa la ubicación declarada")).toBeTruthy();
    expect(within(form).getByLabelText("Ubicación declarada").getAttribute("aria-invalid")).toBe(
      "true",
    );
    expect(within(form).queryByRole("alert")).toBeNull();
  });

  it("shows a generic alert when creation fails for another reason", async () => {
    fetchMock.mockImplementation((_input, init) =>
      Promise.resolve(
        init?.method === "POST"
          ? jsonResponse({ title: "Forbidden", status: 403 }, 403)
          : jsonResponse({ items: EXISTING }),
      ),
    );
    render(<CaseListPage />);
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "Nuevo expediente" }));
    const form = screen.getByRole("form", { name: "Nuevo expediente" });
    fireEvent.change(within(form).getByLabelText("Descripción"), { target: { value: "Hecho" } });
    fireEvent.change(within(form).getByLabelText("Ubicación declarada"), {
      target: { value: "Calle 1" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Crear expediente" }));

    const alert = await within(form).findByRole("alert");
    expect(alert.textContent).toBe("No se pudo crear el expediente. Inténtalo de nuevo.");
    expect(tableRows()).toHaveLength(2);
  });
});
