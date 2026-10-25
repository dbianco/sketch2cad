import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders the shell heading", () => {
    renderAt("/");

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("Sketch2CAD");
  });

  it("renders the home route placeholder at the root path", () => {
    renderAt("/");

    expect(screen.getByText("Aplicación en construcción.")).toBeTruthy();
  });

  it("renders the not found placeholder for an unknown path", () => {
    renderAt("/does-not-exist");

    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
    expect(screen.getByText("Página no encontrada.")).toBeTruthy();
  });
});
