import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_GRID_M,
  DEFAULT_SNAP_ENABLED,
  GRID_STORAGE_KEY,
  SNAP_TEXT,
  SnapSettings,
  createSnapSettingsStore,
  defaultStorage,
  loadGridSize,
} from "./SnapSettings";

/** A Storage whose every access throws, as in some private modes. */
function throwingStorage(): Storage {
  const fail = () => {
    throw new DOMException("denied", "SecurityError");
  };
  return {
    length: 0,
    clear: fail,
    getItem: fail,
    key: fail,
    removeItem: fail,
    setItem: fail,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("grid size persistence", () => {
  it("persists the grid size in localStorage under s2c.grid and reads it back", () => {
    const snap = createSnapSettingsStore();
    expect(snap.getState().gridM).toBe(DEFAULT_GRID_M);
    expect(snap.getState().setGridM(0.25)).toBe(true);
    expect(window.localStorage.getItem(GRID_STORAGE_KEY)).toBe("0.25");
    expect(createSnapSettingsStore().getState().gridM).toBe(0.25);
  });

  it("rejects a grid size that is not a finite number greater than 0", () => {
    const snap = createSnapSettingsStore();
    for (const value of [0, -0.5, NaN, Infinity]) {
      expect(snap.getState().setGridM(value)).toBe(false);
    }
    expect(snap.getState().gridM).toBe(DEFAULT_GRID_M);
    expect(window.localStorage.getItem(GRID_STORAGE_KEY)).toBeNull();
  });

  it("falls back to the default for a stored value that is invalid", () => {
    for (const raw of ["", "abc", "0", "-1", "Infinity"]) {
      window.localStorage.setItem(GRID_STORAGE_KEY, raw);
      expect(loadGridSize()).toBe(DEFAULT_GRID_M);
    }
  });

  it("falls back to the defaults when the storage throws, and still applies changes", () => {
    const snap = createSnapSettingsStore(throwingStorage());
    expect(snap.getState().gridM).toBe(DEFAULT_GRID_M);
    expect(snap.getState().enabled).toEqual(DEFAULT_SNAP_ENABLED);
    expect(snap.getState().setGridM(0.5)).toBe(true);
    expect(snap.getState().gridM).toBe(0.5);
  });

  it("treats a localStorage getter that throws as no storage", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(defaultStorage()).toBeNull();
    expect(loadGridSize()).toBe(DEFAULT_GRID_M);
  });

  it("persists the snap toggles under s2c.snap.*", () => {
    const snap = createSnapSettingsStore();
    snap.getState().setEnabled("grid", true);
    snap.getState().setEnabled("endpoint", false);
    expect(window.localStorage.getItem("s2c.snap.grid")).toBe("true");
    expect(createSnapSettingsStore().getState().enabled).toEqual({
      endpoint: false,
      orthogonal: true,
      grid: true,
    });
  });
});

describe("SnapSettings panel", () => {
  it("has labelled toggles and a grid size input that validates and persists", () => {
    const snap = createSnapSettingsStore();
    render(<SnapSettings snap={snap} />);
    expect(screen.getByRole("region", { name: SNAP_TEXT.panel })).toBeDefined();

    const grid = screen.getByRole("checkbox", { name: SNAP_TEXT.grid });
    expect((grid as HTMLInputElement).checked).toBe(false);
    fireEvent.click(grid);
    expect(snap.getState().enabled.grid).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: SNAP_TEXT.orthogonal }));
    expect(snap.getState().enabled.orthogonal).toBe(false);
    expect(screen.getByRole("checkbox", { name: SNAP_TEXT.endpoint })).toBeDefined();

    const size = screen.getByRole("spinbutton", { name: SNAP_TEXT.gridSize });
    fireEvent.change(size, { target: { value: "0.2" } });
    expect(snap.getState().gridM).toBe(0.2);
    expect(window.localStorage.getItem(GRID_STORAGE_KEY)).toBe("0.2");
    expect(screen.queryByRole("alert")).toBeNull();

    fireEvent.change(size, { target: { value: "0" } });
    expect(screen.getByRole("alert").textContent).toBe(SNAP_TEXT.invalidGrid);
    expect(size.getAttribute("aria-invalid")).toBe("true");
    expect(snap.getState().gridM).toBe(0.2);
    expect(window.localStorage.getItem(GRID_STORAGE_KEY)).toBe("0.2");
  });
});
