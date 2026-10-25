// Catalog symbol previews as React elements, without innerHTML.
//
// The API only accepts static symbol SVG from a small allow list (catalog/schema.py,
// `check_safe_svg`). The client does not trust that: it parses the markup with DOMParser and
// rebuilds it with React elements, copying only allow-listed elements and attributes. Anything
// else (scripts, handlers, links, styles, text) makes the whole symbol rejected.
//
// Catalog convention: 100 SVG user units per block unit, the anchor at (0, 0), Y down, drawn
// with `currentColor` so the layer color applies.
import { createElement, type ReactElement } from "react";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

/** SVG user units of the catalog markup per block unit. */
export const SYMBOL_UNITS_PER_BLOCK = 100;

const SHAPE_ELEMENTS: ReadonlySet<string> = new Set([
  "g",
  "path",
  "line",
  "circle",
  "rect",
  "polyline",
  "polygon",
]);

/** Allow-listed attributes and their React prop names. */
const ATTRIBUTE_PROPS: Readonly<Record<string, string>> = {
  d: "d",
  x: "x",
  y: "y",
  x1: "x1",
  y1: "y1",
  x2: "x2",
  y2: "y2",
  cx: "cx",
  cy: "cy",
  r: "r",
  width: "width",
  height: "height",
  points: "points",
  transform: "transform",
  fill: "fill",
  stroke: "stroke",
  "stroke-linecap": "strokeLinecap",
  "stroke-linejoin": "strokeLinejoin",
  "stroke-dasharray": "strokeDasharray",
};

/** Accepted by the API allow list but dropped: the canvas sets screen stroke widths itself. */
const IGNORED_ATTRIBUTES: ReadonlySet<string> = new Set(["viewBox", "stroke-width", "xmlns"]);

const FORBIDDEN_TEXT = /<!|<\?|url\(|javascript:/i;

class UnsafeSymbolError extends Error {}

function convert(element: Element, key: number): ReactElement {
  if (element.namespaceURI !== SVG_NAMESPACE || !SHAPE_ELEMENTS.has(element.localName)) {
    throw new UnsafeSymbolError(`Element not allowed: ${element.tagName}`);
  }
  const props: Record<string, string | number> = { key };
  for (const attribute of Array.from(element.attributes)) {
    const prop = ATTRIBUTE_PROPS[attribute.name];
    if (prop !== undefined) props[prop] = attribute.value;
    else if (!IGNORED_ATTRIBUTES.has(attribute.name)) {
      throw new UnsafeSymbolError(`Attribute not allowed: ${attribute.name}`);
    }
  }
  if (element.localName !== "g") props.vectorEffect = "non-scaling-stroke";
  return createElement(element.localName, props, ...childElements(element));
}

function childElements(element: Element): ReactElement[] {
  for (const node of Array.from(element.childNodes)) {
    if (node.nodeType === node.TEXT_NODE && (node.textContent ?? "").trim() !== "") {
      throw new UnsafeSymbolError("Text content is not allowed");
    }
  }
  return Array.from(element.children).map((child, index) => convert(child, index));
}

/**
 * Children of the catalog `<svg>` rebuilt as React elements, or `null` when the markup is not
 * well-formed or uses anything outside the allow list.
 */
export function parseSymbolSvg(markup: string): ReactElement[] | null {
  if (FORBIDDEN_TEXT.test(markup)) return null;
  const document = new DOMParser().parseFromString(markup, "image/svg+xml");
  const root = document.documentElement;
  if (
    document.getElementsByTagName("parsererror").length > 0 ||
    root.namespaceURI !== SVG_NAMESPACE ||
    root.localName !== "svg"
  ) {
    return null;
  }
  try {
    for (const attribute of Array.from(root.attributes)) {
      if (!IGNORED_ATTRIBUTES.has(attribute.name)) {
        throw new UnsafeSymbolError(`Attribute not allowed on <svg>: ${attribute.name}`);
      }
    }
    return childElements(root);
  } catch (error) {
    if (error instanceof UnsafeSymbolError) return null;
    throw error;
  }
}
