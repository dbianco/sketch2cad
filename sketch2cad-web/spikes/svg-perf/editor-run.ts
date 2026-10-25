/// <reference types="node" />
/**
 * H2-12 runner (task 001-068, REQ-05): builds editor.html + editor-entry.tsx (the REAL
 * EditorCanvas with 2000 entities) with Vite in production mode into a temporary folder, serves
 * it on a free local port, measures in Playwright chromium headed and then headless, and writes
 * editor-result.json next to this file. Run from sketch2cad-web/ with `pnpm spike:editor`.
 *
 * `--smoke` (CI): headless only, 1.5 s route, writes to the OS temp folder instead, and only
 * checks that the harness runs end to end.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Browser } from "@playwright/test";
import react from "@vitejs/plugin-react";
import { build } from "vite";

type Mode = "headed" | "headless";
type Scenario = "pan-zoom" | "select-move-vertex";

interface RunResult {
  scenario: Scenario;
  median_fps: number;
  p95_frame_ms: number;
  frames: number;
  duration_s: number;
  refresh_hz_probe: number;
  entities: number;
  seed: number;
  [key: string]: unknown;
}

interface MountResult {
  entities: number;
  rendered_entity_elements: number;
  store_load_ms: number;
  render_commit_ms: number;
  first_paint_ms: number;
}

const THRESHOLDS = { median_fps: 60, p95_frame_ms: 25 };
/**
 * "At refresh cap": a median frame interval within this fraction of the display's own interval
 * (refresh_hz_probe) means no frame budget was missed, so a 60 Hz display passes at ~59.9 fps.
 */
const REFRESH_CAP_TOLERANCE = 0.02;
const SMOKE = process.argv.includes("--smoke");
const ROUTE = SMOKE ? { durationMs: 1500, warmupMs: 500 } : { durationMs: 10000, warmupMs: 1000 };
const EDIT_ROUTE = SMOKE ? { durationMs: 1500, warmupMs: 500 } : { durationMs: 5000, warmupMs: 1000 };
const MODES: Mode[] = SMOKE ? ["headless"] : ["headed", "headless"];
const VIEWPORT = { width: 1280, height: 800 };
// Keep a headed window that loses focus or gets occluded from being throttled.
const LAUNCH_ARGS = [
  "--disable-background-timer-throttling",
  "--disable-backgrounding-occluded-windows",
  "--disable-renderer-backgrounding",
];

const here = path.dirname(fileURLToPath(import.meta.url));

function sh(cmd: string, args: string[]): string {
  try {
    return execFileSync(cmd, args, { encoding: "utf8", timeout: 20000 }).trim();
  } catch {
    return "";
  }
}

// Same facts as run.ts: model-level hardware only; no serial numbers, hostnames or user names.
function hardwareInfo(): Record<string, unknown> {
  const displays: { name: string; resolution: string; main: boolean }[] = [];
  let gpu = "unknown";
  if (process.platform === "darwin") {
    try {
      const data = JSON.parse(sh("system_profiler", ["SPDisplaysDataType", "-json"]) || "{}") as {
        SPDisplaysDataType?: (Record<string, string> & { spdisplays_ndrvs?: Record<string, string>[] })[];
      };
      for (const card of data.SPDisplaysDataType ?? []) {
        gpu = `${card.sppci_model ?? card._name ?? "unknown"}${card.sppci_cores ? `, ${card.sppci_cores} cores` : ""}`;
        for (const d of card.spdisplays_ndrvs ?? []) {
          displays.push({
            name: d._name ?? "unknown",
            resolution: d._spdisplays_resolution ?? d.spdisplays_resolution ?? "unknown",
            main: d.spdisplays_main === "spdisplays_yes",
          });
        }
      }
    } catch {
      // Display details are optional.
    }
  }
  return {
    cpu: sh("sysctl", ["-n", "machdep.cpu.brand_string"]) || (os.cpus()[0]?.model ?? "unknown"),
    cores: os.cpus().length,
    gpu,
    ram_gb: Math.round(os.totalmem() / 1024 ** 3),
    model: sh("sysctl", ["-n", "hw.model"]) || "unknown",
    os:
      process.platform === "darwin"
        ? `macOS ${sh("sw_vers", ["-productVersion"])} (${os.arch()})`
        : `${os.type()} ${os.release()} (${os.arch()})`,
    displays,
  };
}

/** Production build of the harness page (the app's own build never sees these files). */
async function buildPage(outDir: string): Promise<void> {
  await build({
    root: here,
    configFile: false,
    logLevel: "warn",
    mode: "production",
    plugins: [react()],
    build: {
      outDir,
      emptyOutDir: true,
      rollupOptions: { input: path.join(here, "editor.html") },
    },
  });
}

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

function startServer(root: string): Promise<{ url: string; close: () => Promise<void> }> {
  const server = createServer((req, res) => {
    const name = req.url === "/" || req.url === undefined ? "/editor.html" : req.url.split("?")[0];
    const file = path.normalize(path.join(root, name ?? ""));
    try {
      if (!file.startsWith(root)) throw new Error("outside root");
      const body = readFileSync(file);
      res.writeHead(200, { "content-type": CONTENT_TYPES[path.extname(file)] ?? "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404);
      res.end();
    }
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as AddressInfo;
      resolve({
        url: `http://127.0.0.1:${port}/`,
        close: () => new Promise<void>((done) => server.close(() => done())),
      });
    });
  });
}

/** One fresh page: mount the editor, then run one scenario. */
async function measure(
  browser: Browser,
  url: string,
  scenario: Scenario,
): Promise<{ mount: MountResult; result: RunResult }> {
  const context = await browser.newContext({ viewport: VIEWPORT });
  try {
    const page = await context.newPage();
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto(url);
    await page.waitForFunction(() => (window as unknown as { __spikeReady?: boolean }).__spikeReady);
    await page.bringToFront();
    const mount = await page.evaluate(() =>
      (window as unknown as { __mountEditor: () => Promise<MountResult> }).__mountEditor(),
    );
    const fn = scenario === "pan-zoom" ? "__runPanZoom" : "__runEdit";
    const options = scenario === "pan-zoom" ? ROUTE : EDIT_ROUTE;
    const result = await page.evaluate(([name, o]) => {
      const run = (window as unknown as Record<string, (o: unknown) => Promise<RunResult>>)[name];
      if (run === undefined) throw new Error(`Harness function ${name} missing`);
      return run(o);
    }, [fn, options] as const);
    if (pageErrors.length > 0) throw new Error(`Page errors: ${pageErrors.join("; ")}`);
    return { mount, result };
  } finally {
    await context.close();
  }
}

async function launch(mode: Mode): Promise<Browser | null> {
  try {
    return await chromium.launch({ headless: mode === "headless", args: LAUNCH_ARGS });
  } catch (error) {
    console.warn(`Could not launch ${mode} chromium: ${(error as Error).message.split("\n")[0]}`);
    return null;
  }
}

const passesStrict = (r: RunResult): boolean =>
  r.median_fps >= THRESHOLDS.median_fps && r.p95_frame_ms <= THRESHOLDS.p95_frame_ms;

/** Median at the display's refresh cap (no missed budget) and p95 within threshold. */
function passesAtCap(r: RunResult): boolean {
  const atCap = r.median_fps >= r.refresh_hz_probe * (1 - REFRESH_CAP_TOLERANCE);
  return (passesStrict(r) || atCap) && r.p95_frame_ms <= THRESHOLDS.p95_frame_ms;
}

async function main(): Promise<void> {
  const outDir = mkdtempSync(path.join(os.tmpdir(), "s2c-editor-perf-"));
  const runs: (RunResult & { mode: Mode; browser: string; mount: MountResult })[] = [];
  try {
    await buildPage(outDir);
    const server = await startServer(outDir);
    try {
      for (const mode of MODES) {
        const browser = await launch(mode);
        if (browser === null) continue;
        try {
          const name = `${browser.browserType().name()} ${browser.version()}`;
          for (const scenario of ["pan-zoom", "select-move-vertex"] as Scenario[]) {
            const { mount, result } = await measure(browser, server.url, scenario);
            runs.push({ ...result, mode, browser: name, mount });
            console.log(
              `${mode.padEnd(8)} ${scenario.padEnd(18)} median_fps=${result.median_fps} ` +
                `p95_frame_ms=${result.p95_frame_ms} frames=${result.frames} ` +
                `refresh_hz=${result.refresh_hz_probe} mount_first_paint_ms=${mount.first_paint_ms}`,
            );
          }
        } finally {
          await browser.close();
        }
      }
    } finally {
      await server.close();
    }
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }

  const primary =
    runs.find((r) => r.mode === "headed" && r.scenario === "pan-zoom") ??
    runs.find((r) => r.scenario === "pan-zoom");
  if (primary === undefined) throw new Error("No measurement run completed");
  const edit = runs.find((r) => r.mode === primary.mode && r.scenario === "select-move-vertex");
  const passes = passesAtCap(primary);

  const output = {
    median_fps: primary.median_fps,
    p95_frame_ms: primary.p95_frame_ms,
    entities: primary.entities,
    browser: primary.browser,
    hardware: hardwareInfo(),
    duration_s: primary.duration_s,
    frames: primary.frames,
    technique: "viewbox (real EditorCanvas)",
    scenario: primary.scenario,
    mode: primary.mode,
    smoke: SMOKE,
    date: new Date().toISOString(),
    seed: primary.seed,
    refresh_hz_probe: primary.refresh_hz_probe,
    thresholds: THRESHOLDS,
    refresh_cap_tolerance: REFRESH_CAP_TOLERANCE,
    passes_strict: passesStrict(primary),
    passes,
    decision: passes ? "keep-svg" : "recommend-konva-h3-blocked",
    scenarios: {
      pan_zoom: { median_fps: primary.median_fps, p95_frame_ms: primary.p95_frame_ms },
      select_move_vertex:
        edit === undefined
          ? null
          : { median_fps: edit.median_fps, p95_frame_ms: edit.p95_frame_ms, work_per_frame_ms: edit.work_per_frame_ms },
      initial_mount: primary.mount,
    },
    runs,
  };
  const target = SMOKE ? path.join(os.tmpdir(), "editor-result.smoke.json") : path.join(here, "editor-result.json");
  writeFileSync(target, `${JSON.stringify(output, null, 2)}\n`);
  console.log(
    `Primary (${primary.mode}, ${primary.scenario}): median_fps=${primary.median_fps} ` +
      `p95_frame_ms=${primary.p95_frame_ms} refresh_hz=${primary.refresh_hz_probe} -> ` +
      `${output.decision}${SMOKE ? " (smoke run, not a measurement)" : ""}. Wrote ${target}`,
  );
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
