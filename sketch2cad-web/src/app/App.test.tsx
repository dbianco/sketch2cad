import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authStore } from "../features/auth/authStore";
import { App } from "./App";

const USER = {
  id: "8a7c0d4e-0000-4000-8000-000000000001",
  username: "ana",
  display_name: null,
  role: "investigator",
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  beforeEach(() => {
    authStore.reset();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() =>
        Promise.resolve(
          new Response(JSON.stringify({ user: USER }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows a loading status while the session is resolved", () => {
    renderAt("/");

    expect(screen.getByRole("status").textContent).toBe("Cargando…");
  });

  it("renders the shell heading", async () => {
    renderAt("/");

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("Sketch2CAD");
  });

  it("renders the home route placeholder at the root path", async () => {
    renderAt("/");

    expect(await screen.findByText("Aplicación en construcción.")).toBeTruthy();
    expect(screen.getByText("ana")).toBeTruthy();
  });

  it("renders the not found placeholder for an unknown path", async () => {
    renderAt("/does-not-exist");

    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    expect(screen.getByText("Página no encontrada.")).toBeTruthy();
  });
});
