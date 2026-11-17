import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PREVIEW_MAX_SIDE, SourceViewer, VIEWER_MESSAGES, previewUrl } from "./SourceViewer";

const SOURCE_ID = "00000000-0000-4000-8000-0000000000s1";
const PREVIEW_URL = `/api/v1/sources/${SOURCE_ID}/preview?max=2048`;
const OBJECT_URL = "blob:http://localhost/preview-1";

const fetchMock = vi.fn<typeof fetch>();
const createObjectURL = vi.fn<(blob: Blob) => string>(() => OBJECT_URL);
const revokeObjectURL = vi.fn<(url: string) => void>();

function jpegResponse(): Response {
  return new Response(new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }), {
    status: 200,
    headers: { "Content-Type": "image/jpeg" },
  });
}

function requestedUrls(): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input));
}

function viewer(): HTMLElement {
  return screen.getByTestId("source-viewer");
}

function currentScale(): number {
  return Number(viewer().dataset.scale);
}

async function renderReady(): Promise<void> {
  render(<SourceViewer sourceId={SOURCE_ID} alt="Croquis del cruce" />);
  await screen.findByRole("img", { name: "Croquis del cruce" });
}

beforeEach(() => {
  fetchMock.mockReset();
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  // jsdom implements neither object URLs nor layout.
  Object.assign(URL, { createObjectURL, revokeObjectURL });
  // Give the zoom wrapper a size so the library can compute the zoom centre.
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("previewUrl", () => {
  it("builds the preview URL with the default max side", () => {
    expect(PREVIEW_MAX_SIDE).toBe(2048);
    expect(previewUrl(SOURCE_ID)).toBe(PREVIEW_URL);
  });

  it("encodes the source id", () => {
    expect(previewUrl("a/b", 512)).toBe("/api/v1/sources/a%2Fb/preview?max=512");
  });
});

describe("SourceViewer", () => {
  beforeEach(() => {
    fetchMock.mockImplementation(() => Promise.resolve(jpegResponse()));
  });

  it("requests only the preview URL with the session cookie, never the original", async () => {
    await renderReady();

    expect(requestedUrls()).toEqual([PREVIEW_URL]);
    expect(requestedUrls().some((url) => url.includes("/original"))).toBe(false);
    expect(fetchMock.mock.calls[0]?.[1]?.credentials).toBe("include");
    expect(screen.getByRole("img", { name: "Croquis del cruce" }).getAttribute("src")).toBe(OBJECT_URL);
  });

  it("labels the zoom buttons for assistive technology", async () => {
    await renderReady();

    for (const label of ["Acercar", "Alejar", "Restablecer el zoom al 100 %"]) {
      const button = screen.getByRole("button", { name: label });
      expect(button.getAttribute("aria-label")).toBe(label);
      expect((button as HTMLButtonElement).disabled).toBe(false);
    }
  });

  it("increases the scale when + is pressed in the focused viewer", async () => {
    await renderReady();
    expect(currentScale()).toBe(1);

    act(() => viewer().focus());
    fireEvent.keyDown(viewer(), { key: "+" });

    await waitFor(() => expect(currentScale()).toBeGreaterThan(1));
  });

  it("zooms out with - and resets with 0", async () => {
    await renderReady();
    fireEvent.keyDown(viewer(), { key: "+" });
    fireEvent.keyDown(viewer(), { key: "+" });
    await waitFor(() => expect(currentScale()).toBeGreaterThan(1));
    const zoomed = currentScale();

    fireEvent.keyDown(viewer(), { key: "-" });
    await waitFor(() => expect(currentScale()).toBeLessThan(zoomed));

    fireEvent.keyDown(viewer(), { key: "0" });
    await waitFor(() => expect(currentScale()).toBe(1));
  });

  it("zooms with the toolbar buttons", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: "Acercar" }));
    await waitFor(() => expect(currentScale()).toBeGreaterThan(1));

    fireEvent.click(screen.getByRole("button", { name: "Restablecer el zoom al 100 %" }));
    await waitFor(() => expect(currentScale()).toBe(1));
  });

  it("ignores shortcuts with modifier keys or typed into a text field", async () => {
    render(
      <div>
        <SourceViewer sourceId={SOURCE_ID} alt="Croquis del cruce" />
        <input aria-label="Nota" />
      </div>,
    );
    await screen.findByRole("img", { name: "Croquis del cruce" });

    fireEvent.keyDown(viewer(), { key: "+", ctrlKey: true });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Nota" }), { key: "+" });
    fireEvent.keyDown(document.body, { key: "+" });

    expect(currentScale()).toBe(1);
  });

  it("shows an error when the preview is denied", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(null, { status: 403 })));
    render(<SourceViewer sourceId={SOURCE_ID} alt="Croquis del cruce" />);

    expect((await screen.findByRole("alert")).textContent).toBe(VIEWER_MESSAGES.denied);
    expect(screen.queryByRole("img")).toBeNull();
    expect((screen.getByRole("button", { name: "Acercar" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a generic error when the request fails", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError("network down")));
    render(<SourceViewer sourceId={SOURCE_ID} alt="Croquis del cruce" />);

    expect((await screen.findByRole("alert")).textContent).toBe(VIEWER_MESSAGES.generic);
  });

  it("revokes the object URL on unmount", async () => {
    await renderReady();

    cleanup();

    expect(revokeObjectURL).toHaveBeenCalledWith(OBJECT_URL);
  });
});

describe("SourceViewer keyboard marking", () => {
  it("moves a cursor with the arrows and marks the point with Enter or Space", async () => {
    fetchMock.mockResolvedValue(jpegResponse());
    const onPoint = vi.fn<(px: [number, number]) => void>();
    render(
      <SourceViewer
        sourceId={SOURCE_ID}
        alt="Croquis del cruce"
        marking={{ imageSize: { width: 2000, height: 1000 }, points: [], onPoint }}
      />,
    );
    await screen.findByRole("img", { name: "Croquis del cruce" });
    expect(screen.getByText(VIEWER_MESSAGES.markingKeys)).toBeTruthy();

    fireEvent.keyDown(viewer(), { key: "ArrowRight" });
    expect(screen.getByTestId("source-cursor")).toBeTruthy();
    expect(screen.getByText(VIEWER_MESSAGES.cursorAt([1020, 500]))).toBeTruthy();
    fireEvent.keyDown(viewer(), { key: "Enter" });
    expect(onPoint).toHaveBeenLastCalledWith([1020, 500]);

    fireEvent.keyDown(viewer(), { key: "ArrowDown", shiftKey: true });
    fireEvent.keyDown(viewer(), { key: "ArrowLeft", altKey: true });
    fireEvent.keyDown(viewer(), { key: " " });
    expect(onPoint).toHaveBeenLastCalledWith([820, 502]);

    // Keys on the zoom buttons keep their own meaning.
    fireEvent.keyDown(screen.getByRole("button", { name: "Acercar" }), { key: "Enter" });
    expect(onPoint).toHaveBeenCalledTimes(2);
  });

  it("keeps the cursor inside the image", async () => {
    fetchMock.mockResolvedValue(jpegResponse());
    const onPoint = vi.fn<(px: [number, number]) => void>();
    render(
      <SourceViewer
        sourceId={SOURCE_ID}
        alt="Croquis del cruce"
        marking={{ imageSize: { width: 100, height: 100 }, points: [], onPoint }}
      />,
    );
    await screen.findByRole("img", { name: "Croquis del cruce" });
    for (let i = 0; i < 6; i += 1) fireEvent.keyDown(viewer(), { key: "ArrowUp", ctrlKey: true });
    fireEvent.keyDown(viewer(), { key: "Enter" });
    expect(onPoint).toHaveBeenLastCalledWith([50, 0]);
  });
});
