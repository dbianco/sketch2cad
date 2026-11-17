import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { authStore } from "./authStore";

const USER = {
  id: "8a7c0d4e-0000-4000-8000-000000000001",
  username: "ana",
  display_name: "Ana Pérez",
  role: "investigator",
};

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function unauthorized(): Response {
  return jsonResponse({ title: "Unauthorized", status: 401, detail: "Not authenticated" }, 401);
}

type Handler = () => Response | Promise<Response>;

/** Route fetch calls by "METHOD path"; unexpected calls fail the test. */
function mockApi(handlers: Record<string, Handler>): void {
  fetchMock.mockImplementation((input, init) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const handler = handlers[key];
    if (handler === undefined) {
      throw new Error(`Unexpected request ${key}`);
    }
    return Promise.resolve(handler());
  });
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function currentPath(): string {
  return screen.getByTestId("location").textContent ?? "";
}

async function fillAndSubmit(username: string, password: string): Promise<void> {
  const usernameInput = await screen.findByLabelText("Usuario");
  fireEvent.change(usernameInput, { target: { value: username } });
  fireEvent.change(screen.getByLabelText("Contraseña"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
}

describe("LoginPage", () => {
  beforeEach(() => {
    authStore.reset();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("binds a label to every input and renders a single heading", async () => {
    mockApi({ "GET /api/v1/auth/me": unauthorized });
    renderAt("/login");

    const usernameInput = await screen.findByLabelText("Usuario");
    const passwordInput = screen.getByLabelText("Contraseña");

    expect(usernameInput.getAttribute("type")).toBe("text");
    expect(passwordInput.getAttribute("type")).toBe("password");
    expect(passwordInput.getAttribute("autocomplete")).toBe("current-password");
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]?.textContent).toBe("Iniciar sesión");
    expect(document.activeElement).toBe(usernameInput);
  });

  it("sends the credentials and redirects to the home route on success", async () => {
    mockApi({
      "GET /api/v1/auth/me": unauthorized,
      "POST /api/v1/auth/login": () => jsonResponse({ user: USER }),
      "GET /api/v1/case-files": () => jsonResponse({ items: [] }),
    });
    renderAt("/login");

    await fillAndSubmit("ana", "s3cret");

    await waitFor(() => {
      expect(currentPath()).toBe("/");
    });
    expect(screen.getByRole("heading", { level: 1, name: "Expedientes" })).toBeTruthy();
    expect(authStore.getState()).toEqual({ status: "authenticated", user: USER });
    const loginCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(loginCall?.[1]?.body))).toEqual({
      username: "ana",
      password: "s3cret",
    });
  });

  it("shows the invalid credentials message on 401", async () => {
    mockApi({ "GET /api/v1/auth/me": unauthorized, "POST /api/v1/auth/login": unauthorized });
    renderAt("/login");

    await fillAndSubmit("ana", "wrong");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Usuario o contraseña incorrectos.");
    expect(document.activeElement).toBe(alert);
    expect(currentPath()).toBe("/login");
    expect(authStore.getState().status).toBe("anonymous");
  });

  it("shows the unlock time on 423", async () => {
    const lockedUntil = "2026-10-25T12:34:00Z";
    mockApi({
      "GET /api/v1/auth/me": unauthorized,
      "POST /api/v1/auth/login": () =>
        jsonResponse(
          { title: "Locked", status: 423, detail: "Account locked", locked_until: lockedUntil },
          423,
        ),
    });
    renderAt("/login");

    await fillAndSubmit("ana", "wrong");

    const alert = await screen.findByRole("alert");
    const until = new Date(lockedUntil);
    const hhmm = [until.getHours(), until.getMinutes()]
      .map((part) => String(part).padStart(2, "0"))
      .join(":");
    expect(alert.textContent).toContain(
      `Cuenta bloqueada temporalmente. Podrás volver a intentarlo a las ${hhmm} del `,
    );
    expect(alert.textContent).toMatch(/de octubre de 2026\.$/);
  });

  it("shows a generic message for other errors", async () => {
    mockApi({
      "GET /api/v1/auth/me": unauthorized,
      "POST /api/v1/auth/login": () => jsonResponse({ title: "Server error" }, 500),
    });
    renderAt("/login");

    await fillAndSubmit("ana", "s3cret");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("No se pudo iniciar sesión. Inténtalo de nuevo.");
  });

  it("disables the submit button while the request is pending", async () => {
    let respond: (response: Response) => void = () => undefined;
    mockApi({
      "GET /api/v1/auth/me": unauthorized,
      "POST /api/v1/auth/login": () =>
        new Promise<Response>((resolve) => {
          respond = resolve;
        }),
    });
    renderAt("/login");

    await fillAndSubmit("ana", "wrong");

    const button = screen.getByRole("button", { name: "Entrar" });
    expect(button.hasAttribute("disabled")).toBe(true);
    await act(async () => {
      respond(unauthorized());
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(button.hasAttribute("disabled")).toBe(false);
    });
  });
});

describe("authStore and protected routes", () => {
  beforeEach(() => {
    authStore.reset();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loadSession turns a 401 into an anonymous session without an error", async () => {
    mockApi({ "GET /api/v1/auth/me": unauthorized });

    const result = await authStore.loadSession();

    expect(result).toEqual({ ok: false });
    expect(authStore.getState()).toEqual({ status: "anonymous", user: null });
  });

  it("loadSession stores the user returned by /auth/me", async () => {
    mockApi({ "GET /api/v1/auth/me": () => jsonResponse({ user: USER }) });

    await authStore.loadSession();

    expect(authStore.getState()).toEqual({ status: "authenticated", user: USER });
  });

  it("redirects anonymous users from the protected route to the login page", async () => {
    mockApi({ "GET /api/v1/auth/me": unauthorized });
    renderAt("/");

    await screen.findByLabelText("Usuario");
    expect(currentPath()).toBe("/login");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Cerrar sesión" })).toBeNull();
  });

  it("logs out from the header and returns to the login page", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: USER }),
      "POST /api/v1/auth/logout": () => new Response(null, { status: 204 }),
    });
    renderAt("/");

    const logoutButton = await screen.findByRole("button", { name: "Cerrar sesión" });
    expect(screen.getByText("Ana Pérez")).toBeTruthy();
    fireEvent.click(logoutButton);

    await screen.findByLabelText("Usuario");
    expect(currentPath()).toBe("/login");
    expect(authStore.getState()).toEqual({ status: "anonymous", user: null });
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === "/api/v1/auth/logout" && init?.method === "POST",
      ),
    ).toBe(true);
  });
});
