import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../../api/client";
import { EditorWorkspace } from "../EditorWorkspace";
import type { Catalog } from "../canvas/EditorCanvas";
import catalogJson from "../fixtures/catalog-default-1.json";
import { createEditorStore } from "../store/entities";
import { createEditorUiStore } from "../ui/editorUi";
import {
  SYMBOL_PICKER_TEXT,
  SymbolPicker,
  createSymbolPickerStore,
  parseDecimal,
  previewViewBox,
  readPlacement,
} from "./SymbolPicker";

/** Catalog `default-1` as the API serves it (dumped from sketch2cad-api's default catalog). */
const DEFAULT_CATALOG = catalogJson as Catalog;
const MARKER = DEFAULT_CATALOG.symbols.find((s) => s.symbol_id === "evidence_marker");
if (MARKER === undefined) throw new Error("The fixture has no evidence_marker");

function stubClient(...responses: (Catalog | Error)[]) {
  const get = vi.fn(() => {
    const next = responses.shift() ?? new Error("No more responses");
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
  });
  return { client: { get } as unknown as Pick<ApiClient, "get">, get };
}

function symbolButtons(): HTMLElement[] {
  const list = screen.getByRole("list", { name: SYMBOL_PICKER_TEXT.symbols });
  return within(list).getAllByRole("button");
}

afterEach(cleanup);

describe("SymbolPicker catalog", () => {
  it("loads GET /catalog/active and lists exactly its 8 symbols with inline previews", async () => {
    const { client, get } = stubClient(DEFAULT_CATALOG);
    render(<SymbolPicker picker={createSymbolPickerStore()} client={client} />);
    expect(screen.getByRole("status").textContent).toBe(SYMBOL_PICKER_TEXT.loading);

    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());

    expect(get).toHaveBeenCalledWith("/api/v1/catalog/active", expect.anything());
    const buttons = symbolButtons();
    expect(buttons).toHaveLength(8);
    expect(buttons.map((button) => button.getAttribute("data-symbol-id"))).toEqual([
      "north_arrow",
      "evidence_marker",
      "control_point",
      "door",
      "window",
      "generic_vehicle",
      "traffic_light",
      "tree",
    ]);
    for (const [index, symbol] of DEFAULT_CATALOG.symbols.entries()) {
      const button = buttons[index] as HTMLElement;
      expect(screen.getByRole("button", { name: symbol.label })).toBe(button);
      const preview = within(button).getByTestId("symbol-preview");
      expect(preview.getAttribute("aria-hidden")).toBe("true");
      // Rebuilt shapes, not the markup: the stroke width of the catalog is dropped.
      expect(preview.querySelector("circle, rect, line, path, polygon")).not.toBeNull();
      expect(preview.querySelector("[stroke-width]")).toBeNull();
      expect(button.tabIndex).toBe(0);
    }
  });

  it("lists whatever the endpoint returns, not a fixed set", async () => {
    const two = { ...DEFAULT_CATALOG, symbols: DEFAULT_CATALOG.symbols.slice(3, 5) };
    const { client } = stubClient(two);
    render(<SymbolPicker picker={createSymbolPickerStore()} client={client} />);
    await waitFor(() => expect(symbolButtons()).toHaveLength(2));
    expect(symbolButtons().map((b) => b.getAttribute("aria-label"))).toEqual(["Puerta", "Ventana"]);
  });

  it("shows a Spanish error when the catalog cannot be loaded, and retries", async () => {
    const { client, get } = stubClient(new Error("offline"), DEFAULT_CATALOG);
    render(<SymbolPicker picker={createSymbolPickerStore()} client={client} />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(SYMBOL_PICKER_TEXT.loadError);

    fireEvent.click(within(alert).getByRole("button", { name: SYMBOL_PICKER_TEXT.retry }));

    await waitFor(() => expect(symbolButtons()).toHaveLength(8));
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("uses a catalog given by the page without requesting it", () => {
    const { client, get } = stubClient();
    render(
      <SymbolPicker picker={createSymbolPickerStore()} catalog={DEFAULT_CATALOG} client={client} />,
    );
    expect(symbolButtons()).toHaveLength(8);
    expect(get).not.toHaveBeenCalled();
  });
});

describe("SymbolPicker form", () => {
  function renderPicker() {
    const picker = createSymbolPickerStore();
    render(<SymbolPicker picker={picker} catalog={DEFAULT_CATALOG} />);
    return picker;
  }

  it("selects a symbol with its button and generates the form from its attributes", () => {
    const picker = renderPicker();
    fireEvent.click(screen.getByRole("button", { name: "Marcador de indicio" }));
    expect(picker.getState().symbolId).toBe("evidence_marker");
    expect(
      screen.getByRole("button", { name: "Marcador de indicio" }).getAttribute("aria-pressed"),
    ).toBe("true");

    const number = screen.getByRole("textbox", { name: "Número de indicio (obligatorio)" });
    expect(number.getAttribute("inputmode")).toBe("numeric");
    expect(number.getAttribute("aria-required")).toBe("true");
    const label = screen.getByRole("textbox", { name: "Etiqueta" });
    expect(label.getAttribute("inputmode")).toBe("text");
    expect(label.getAttribute("aria-required")).toBe("false");
    const rotation = screen.getByRole("spinbutton", { name: SYMBOL_PICKER_TEXT.rotation });
    expect(rotation.getAttribute("step")).toBe("15");
    expect(screen.getByRole("textbox", { name: SYMBOL_PICKER_TEXT.scale })).toBeTruthy();

    fireEvent.change(number, { target: { value: "7" } });
    fireEvent.change(rotation, { target: { value: "45" } });
    expect(picker.getState().values).toEqual({ evidence_number: "7" });
    expect(picker.getState().rotation).toBe("45");

    fireEvent.click(screen.getByRole("button", { name: "Vehículo genérico" }));
    expect(screen.queryByRole("textbox", { name: /Número de indicio/ })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Etiqueta" })).toBeTruthy();
    expect(picker.getState().values).toEqual({});

    fireEvent.click(screen.getByRole("button", { name: "Puerta" }));
    expect(screen.queryByRole("group", { name: /Atributos/ })).toBeNull();
  });

  it("marks the missing evidence_number invalid with a Spanish message after a blocked placement", () => {
    const picker = renderPicker();
    fireEvent.click(screen.getByRole("button", { name: "Marcador de indicio" }));
    const number = screen.getByRole("textbox", { name: "Número de indicio (obligatorio)" });
    expect(number.getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByRole("alert")).toBeNull();

    act(() => picker.getState().block(null));
    expect(screen.getByRole("alert").textContent).toBe(
      "No se puede colocar «Marcador de indicio»: corrige los campos marcados.",
    );
    const invalid = screen.getByRole("textbox", { name: "Número de indicio (obligatorio)" });
    expect(invalid.getAttribute("aria-invalid")).toBe("true");
    const description = document.getElementById(invalid.getAttribute("aria-describedby") ?? "");
    expect(description?.textContent).toBe(
      "Completa «Número de indicio»: es obligatorio para colocar este símbolo.",
    );

    fireEvent.change(invalid, { target: { value: "2,5" } });
    expect(description?.textContent).toBe(
      "«Número de indicio» debe ser un número entero, por ejemplo 3.",
    );
    fireEvent.change(invalid, { target: { value: "3" } });
    expect(invalid.getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("blocked placement on the canvas", () => {
  it("does not place evidence_marker without evidence_number and says why in Spanish", () => {
    const store = createEditorStore();
    store.getState().load("00000000-0000-4000-8000-0000000000r1", []);
    const ui = createEditorUiStore({ activeTool: "symbol" });
    const symbols = createSymbolPickerStore({ symbolId: "evidence_marker" });
    render(
      <EditorWorkspace
        catalog={DEFAULT_CATALOG}
        store={store}
        ui={ui}
        symbols={symbols}
        width={800}
        height={600}
      />,
    );
    const svg = screen.getByTestId("editor-canvas");
    fireEvent.pointerDown(svg, { pointerId: 1, button: 0, clientX: 200, clientY: 200 });
    fireEvent.pointerUp(svg, { pointerId: 1, button: 0, clientX: 200, clientY: 200 });

    expect(store.getState().entities.order).toEqual([]);
    expect(store.getState().queue.ops).toEqual([]);
    expect(screen.getByRole("alert").textContent).toBe(
      SYMBOL_PICKER_TEXT.blocked("Marcador de indicio"),
    );
    expect(
      screen
        .getByRole("textbox", { name: "Número de indicio (obligatorio)" })
        .getAttribute("aria-invalid"),
    ).toBe("true");
  });
});

describe("readPlacement", () => {
  const base = { rotation: "0", scale: "1", values: {} };

  it("parses integer, number and string attributes and drops empty optional ones", () => {
    const symbol = {
      ...MARKER,
      attributes: [
        { name: "evidence_number", type: "integer" as const, required: true },
        { name: "width", type: "number" as const, required: false },
        { name: "label", type: "string" as const, required: false },
        { name: "note", type: "string" as const, required: false },
      ],
    };
    expect(
      readPlacement(symbol, {
        rotation: "-12,5",
        scale: "1.5",
        values: { evidence_number: " 12 ", width: "0,75", label: " Casquillo ", note: "  " },
      }),
    ).toEqual({
      ok: true,
      rotationDeg: -12.5,
      scale: 1.5,
      attributes: { evidence_number: 12, width: 0.75, label: "Casquillo" },
    });
  });

  it("reports invalid rotation, non-positive scale and non-numeric values", () => {
    const result = readPlacement(
      { ...MARKER, attributes: [{ name: "width", type: "number", required: false }] },
      { rotation: "noventa", scale: "0", values: { width: "abc" } },
    );
    expect(result).toEqual({
      ok: false,
      errors: {
        rotation: SYMBOL_PICKER_TEXT.invalidRotation,
        scale: SYMBOL_PICKER_TEXT.invalidScale,
        attributes: { width: "«width» debe ser un número, por ejemplo 2,5." },
      },
    });
    expect(readPlacement(MARKER, base).ok).toBe(false);
    expect(readPlacement(MARKER, { ...base, values: { evidence_number: "4" } }).ok).toBe(true);
  });

  it("parses decimals with comma or point only", () => {
    expect(parseDecimal("3,50")).toBe(3.5);
    expect(parseDecimal(".5")).toBe(0.5);
    expect(parseDecimal("1e3")).toBeNull();
    expect(parseDecimal("")).toBeNull();
  });

  it("takes the preview viewBox from the catalog markup", () => {
    expect(previewViewBox(MARKER.svg)).toBe("-50 -50 100 100");
    expect(previewViewBox("<svg xmlns='http://www.w3.org/2000/svg'/>")).toBe("-50 -50 100 100");
    expect(previewViewBox("<svg viewBox='0 0 a 1'/>")).toBe("-50 -50 100 100");
  });
});
