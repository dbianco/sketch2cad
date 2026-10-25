// Symbol picker (jotdown H2, spec 12.2 and 12.3): the catalog symbols with inline SVG previews,
// the rotation and scale of the next symbol and a form generated from the symbol's attribute
// definitions. The symbol tool (tools/SymbolTool.tsx) reads these choices from the picker store
// and places the symbol; when a required attribute is missing or invalid the placement is
// blocked and the picker shows why. Previews are rebuilt from the catalog markup with the
// allow-list parser of symbolArt.ts; no markup is ever injected as HTML.
//
// Every control is a native button or input, so the picker is reachable with Tab (spec 17).
import { useEffect, useMemo, useState } from "react";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";
import { api as defaultApi, type ApiClient } from "../../api/client";
import type { Catalog } from "../canvas/EditorCanvas";
import { parseSymbolSvg } from "../canvas/symbolArt";
import type { CatalogSymbol } from "../canvas/symbols";

/** Step of the rotation input, in degrees. */
export const ROTATION_STEP_DEG = 15;
const DEFAULT_PREVIEW_VIEW_BOX = "-50 -50 100 100";
const PREVIEW_SIZE_PX = 32;

/** Spanish captions of the attribute names of the default catalog; others show their name. */
const ATTRIBUTE_LABELS: Readonly<Record<string, string>> = {
  evidence_number: "Número de indicio",
  label: "Etiqueta",
};

export const SYMBOL_PICKER_TEXT = {
  panel: "Símbolos",
  symbols: "Símbolos del catálogo",
  loading: "Cargando símbolos…",
  loadError: "No se pudieron cargar los símbolos del catálogo.",
  retry: "Reintentar",
  empty: "El catálogo no tiene símbolos.",
  rotation: "Giro (grados)",
  scale: "Escala",
  required: "obligatorio",
  attributes: (symbol: string) => `Atributos de ${symbol}`,
  attributeLabel: (name: string) => ATTRIBUTE_LABELS[name] ?? name,
  missing: (field: string) => `Completa «${field}»: es obligatorio para colocar este símbolo.`,
  notInteger: (field: string) => `«${field}» debe ser un número entero, por ejemplo 3.`,
  notNumber: (field: string) => `«${field}» debe ser un número, por ejemplo 2,5.`,
  invalidRotation: "El giro debe ser un número de grados, por ejemplo 90.",
  invalidScale: "La escala debe ser un número mayor que 0, por ejemplo 1,5.",
  blocked: (symbol: string) => `No se puede colocar «${symbol}»: corrige los campos marcados.`,
  noSymbol: "Elige un símbolo antes de colocarlo en el plano.",
  layerNotDrawable: (layer: string) =>
    `La capa ${layer} está bloqueada u oculta: no se puede colocar el símbolo en ella.`,
  hint: "Haz clic en el plano para colocar el símbolo. Esc para salir.",
} as const;

export interface SymbolPickerState {
  symbolId: string | null;
  /** Raw inputs, parsed when the symbol is placed. */
  rotation: string;
  scale: string;
  /** Raw input of each attribute of the selected symbol, by attribute name. */
  values: Record<string, string>;
  /** True after a placement was blocked by invalid fields: their errors are then shown. */
  attempted: boolean;
  /** Why the last placement was blocked, when it was not the fields (no symbol, layer). */
  notice: string | null;
}

export interface SymbolPickerActions {
  /** Choose a symbol; its attribute inputs start empty. */
  select(symbolId: string): void;
  setRotation(raw: string): void;
  setScale(raw: string): void;
  setValue(name: string, raw: string): void;
  /** Record a blocked placement: `notice` for a general reason, null for invalid fields. */
  block(notice: string | null): void;
  /** Clear the blocked state after a placement. */
  placed(): void;
}

export type SymbolPickerStore = SymbolPickerState & SymbolPickerActions;

export function createSymbolPickerStore(
  initial: Partial<SymbolPickerState> = {},
): StoreApi<SymbolPickerStore> {
  return createStore<SymbolPickerStore>()((set, get) => ({
    symbolId: null,
    rotation: "0",
    scale: "1",
    values: {},
    attempted: false,
    notice: null,
    ...initial,
    select: (symbolId) => set({ symbolId, values: {}, attempted: false, notice: null }),
    setRotation: (rotation) => set({ rotation }),
    setScale: (scale) => set({ scale }),
    setValue: (name, raw) => set({ values: { ...get().values, [name]: raw } }),
    block: (notice) => set(notice === null ? { attempted: true, notice: null } : { notice }),
    placed: () => set({ attempted: false, notice: null }),
  }));
}

/** The editor's symbol picker store instance. */
export const symbolPicker = createSymbolPickerStore();

/** A number typed with a decimal comma or point (V-04); null when it is not one. */
export function parseDecimal(raw: string): number | null {
  const text = raw.trim().replace(",", ".");
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(text)) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export type SymbolAttributeValues = Record<string, string | number>;

export interface PlacementErrors {
  rotation?: string;
  scale?: string;
  attributes: Record<string, string>;
}

export type Placement =
  | { ok: true; rotationDeg: number; scale: number; attributes: SymbolAttributeValues }
  | { ok: false; errors: PlacementErrors };

/**
 * Rotation, scale and attribute values of `symbol` from the raw inputs, or the Spanish message of
 * every invalid field. Empty optional attributes are left out.
 */
export function readPlacement(
  symbol: CatalogSymbol,
  state: Pick<SymbolPickerState, "rotation" | "scale" | "values">,
): Placement {
  const errors: PlacementErrors = { attributes: {} };
  const rotationDeg = parseDecimal(state.rotation);
  if (rotationDeg === null) errors.rotation = SYMBOL_PICKER_TEXT.invalidRotation;
  const scale = parseDecimal(state.scale);
  if (scale === null || scale <= 0) errors.scale = SYMBOL_PICKER_TEXT.invalidScale;
  const attributes: SymbolAttributeValues = {};
  for (const definition of symbol.attributes) {
    const field = SYMBOL_PICKER_TEXT.attributeLabel(definition.name);
    const raw = (state.values[definition.name] ?? "").trim();
    if (raw === "") {
      if (definition.required) errors.attributes[definition.name] = SYMBOL_PICKER_TEXT.missing(field);
      continue;
    }
    if (definition.type === "string") {
      attributes[definition.name] = raw;
    } else if (definition.type === "integer") {
      if (/^[+-]?\d+$/.test(raw) && Number.isSafeInteger(Number(raw))) {
        attributes[definition.name] = Number(raw);
      } else errors.attributes[definition.name] = SYMBOL_PICKER_TEXT.notInteger(field);
    } else {
      const value = parseDecimal(raw);
      if (value !== null) attributes[definition.name] = value;
      else errors.attributes[definition.name] = SYMBOL_PICKER_TEXT.notNumber(field);
    }
  }
  if (
    rotationDeg === null ||
    scale === null ||
    errors.scale !== undefined ||
    Object.keys(errors.attributes).length > 0
  ) {
    return { ok: false, errors };
  }
  return { ok: true, rotationDeg, scale, attributes };
}

export type CatalogLoad =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; catalog: Catalog };

/** `GET /api/v1/catalog/active`, unless `provided` is given; `retry` loads it again. */
export function useActiveCatalog(
  provided: Catalog | undefined,
  client: Pick<ApiClient, "get">,
): { load: CatalogLoad; retry: () => void } {
  const [fetched, setFetched] = useState<CatalogLoad>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (provided !== undefined) return;
    const controller = new AbortController();
    client.get("/api/v1/catalog/active", { signal: controller.signal }).then(
      (catalog) => setFetched({ status: "ready", catalog }),
      () => {
        if (!controller.signal.aborted) setFetched({ status: "error" });
      },
    );
    return () => controller.abort();
  }, [provided, client, attempt]);
  return {
    load: provided !== undefined ? { status: "ready", catalog: provided } : fetched,
    retry: () => {
      setFetched({ status: "loading" });
      setAttempt((n) => n + 1);
    },
  };
}

/** `viewBox` of the catalog `<svg>` when it is four numbers; a default box otherwise. */
export function previewViewBox(markup: string): string {
  const match = /viewBox\s*=\s*["']([^"']*)["']/.exec(markup);
  const numbers = (match?.[1] ?? "").trim().split(/[\s,]+/).map(Number);
  return numbers.length === 4 && numbers.every(Number.isFinite)
    ? numbers.join(" ")
    : DEFAULT_PREVIEW_VIEW_BOX;
}

function SymbolPreview({ symbol }: { symbol: CatalogSymbol }) {
  const art = useMemo(() => parseSymbolSvg(symbol.svg), [symbol.svg]);
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      data-testid="symbol-preview"
      width={PREVIEW_SIZE_PX}
      height={PREVIEW_SIZE_PX}
      viewBox={previewViewBox(symbol.svg)}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
    >
      {art}
    </svg>
  );
}

function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (message === undefined) return null;
  return (
    <span id={id} className="field-error">
      {message}
    </span>
  );
}

function PlacementForm({
  picker,
  symbol,
}: {
  picker: StoreApi<SymbolPickerStore>;
  symbol: CatalogSymbol;
}) {
  const state = useStore(picker);
  const placement = readPlacement(symbol, state);
  const errors = state.attempted && !placement.ok ? placement.errors : { attributes: {} };
  const errorId = (name: string) => `symbol-field-error-${name}`;
  return (
    <div className="editor-symbol-form">
      {state.attempted && !placement.ok && (
        <p role="alert">{SYMBOL_PICKER_TEXT.blocked(symbol.label)}</p>
      )}
      <label>
        {SYMBOL_PICKER_TEXT.rotation}{" "}
        <input
          type="number"
          step={ROTATION_STEP_DEG}
          value={state.rotation}
          aria-invalid={errors.rotation !== undefined}
          aria-describedby={errors.rotation !== undefined ? errorId("rotation") : undefined}
          onChange={(event) => state.setRotation(event.target.value)}
        />
      </label>
      <FieldError id={errorId("rotation")} message={errors.rotation} />
      <label>
        {SYMBOL_PICKER_TEXT.scale}{" "}
        <input
          type="text"
          inputMode="decimal"
          value={state.scale}
          aria-invalid={errors.scale !== undefined}
          aria-describedby={errors.scale !== undefined ? errorId("scale") : undefined}
          onChange={(event) => state.setScale(event.target.value)}
        />
      </label>
      <FieldError id={errorId("scale")} message={errors.scale} />
      {symbol.attributes.length > 0 && (
        <fieldset>
          <legend>{SYMBOL_PICKER_TEXT.attributes(symbol.label)}</legend>
          {symbol.attributes.map((definition) => {
            const message = errors.attributes[definition.name];
            const field = SYMBOL_PICKER_TEXT.attributeLabel(definition.name);
            return (
              <div key={definition.name}>
                <label>
                  {field}
                  {definition.required && ` (${SYMBOL_PICKER_TEXT.required})`}{" "}
                  <input
                    type="text"
                    name={definition.name}
                    inputMode={
                      definition.type === "integer"
                        ? "numeric"
                        : definition.type === "number"
                          ? "decimal"
                          : "text"
                    }
                    required={definition.required}
                    aria-required={definition.required}
                    aria-invalid={message !== undefined}
                    aria-describedby={message !== undefined ? errorId(`attr-${definition.name}`) : undefined}
                    value={state.values[definition.name] ?? ""}
                    onChange={(event) => state.setValue(definition.name, event.target.value)}
                  />
                </label>
                <FieldError id={errorId(`attr-${definition.name}`)} message={message} />
              </div>
            );
          })}
        </fieldset>
      )}
      <p>{SYMBOL_PICKER_TEXT.hint}</p>
    </div>
  );
}

export interface SymbolPickerProps {
  picker: StoreApi<SymbolPickerStore>;
  /** Catalog already loaded by the page; without it the picker loads the active one. */
  catalog?: Catalog;
  /** API client for the catalog request; the default same-origin client when omitted. */
  client?: Pick<ApiClient, "get">;
}

export function SymbolPicker({ picker, catalog, client = defaultApi }: SymbolPickerProps) {
  const { load, retry } = useActiveCatalog(catalog, client);
  const symbolId = useStore(picker, (state) => state.symbolId);
  const notice = useStore(picker, (state) => state.notice);

  let body;
  if (load.status === "loading") {
    body = <p role="status">{SYMBOL_PICKER_TEXT.loading}</p>;
  } else if (load.status === "error") {
    body = (
      <div role="alert">
        {SYMBOL_PICKER_TEXT.loadError}{" "}
        <button type="button" onClick={retry}>
          {SYMBOL_PICKER_TEXT.retry}
        </button>
      </div>
    );
  } else {
    const { symbols } = load.catalog;
    const selected = symbols.find((symbol) => symbol.symbol_id === symbolId);
    body = (
      <>
        {symbols.length === 0 && <p>{SYMBOL_PICKER_TEXT.empty}</p>}
        <ul role="list" aria-label={SYMBOL_PICKER_TEXT.symbols} className="editor-symbol-list">
          {symbols.map((symbol) => (
            <li key={symbol.symbol_id}>
              <button
                type="button"
                aria-label={symbol.label}
                aria-pressed={symbol.symbol_id === symbolId}
                title={symbol.label}
                data-symbol-id={symbol.symbol_id}
                onClick={() => picker.getState().select(symbol.symbol_id)}
              >
                <SymbolPreview symbol={symbol} />
                <span>{symbol.label}</span>
              </button>
            </li>
          ))}
        </ul>
        {selected !== undefined && <PlacementForm picker={picker} symbol={selected} />}
      </>
    );
  }

  return (
    <aside className="editor-symbol-picker" aria-label={SYMBOL_PICKER_TEXT.panel}>
      {body}
      {notice !== null && <p role="alert">{notice}</p>}
    </aside>
  );
}
