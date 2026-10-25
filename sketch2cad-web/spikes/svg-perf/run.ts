/// <reference types="node" />
/**
 * Spike 1 runner (decision D-2, criterion H0-10): serves index.html on a free local port,
 * drives Playwright chromium through the pan and zoom path for each technique, and writes
 * result.json next to this file. Run from sketch2cad-web/ with `pnpm spike:svg`.
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Browser } from "@playwright/test";

type Mode = "headed" | "headless";
type Technique = "group-transform" | "wrapper-css-transform" | "viewbox";

interface RunResult {
  technique: Technique;
  optimized: boolean;
  entities: number;
  seed: number;
  median_fps: number;
  p95_frame_ms: number;
  frames: number;
  duration_s: number;
  refresh_hz_probe: number;
  [key: string]: unknown;
}

const THRESHOLDS = { median_fps: 60, p95_frame_ms: 25 };
// Fixed before measuring: the editor's planned approach is an <svg> whose content group
// receives the pan/zoom transform; the primary run is that technique in headed chromium.
const PRIMARY_TECHNIQUE: Technique = "group-transform";
const TECHNIQUES: Technique[] = ["group-transform", "wrapper-css-transform", "viewbox"];
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

interface DisplayInfo {
  name: string;
  resolution: string;
  main: boolean;
}

// Collects only model-level hardware facts; no serial numbers, hostnames or user names.
function hardwareInfo(): Record<string, unknown> {
  const displays: DisplayInfo[] = [];
  if (process.platform === "darwin") {
    try {
      const data = JSON.parse(sh("system_profiler", ["SPDisplaysDataType", "-json"]) || "{}") as {
        SPDisplaysDataType?: { spdisplays_ndrvs?: Record<string, string>[] }[];
      };
      for (const gpu of data.SPDisplaysDataType ?? []) {
        for (const d of gpu.spdisplays_ndrvs ?? []) {
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
    ram_gb: Math.round(os.totalmem() / 1024 ** 3),
    model: sh("sysctl", ["-n", "hw.model"]) || "unknown",
    os:
      process.platform === "darwin"
        ? `macOS ${sh("sw_vers", ["-productVersion"])} (${os.arch()})`
        : `${os.type()} ${os.release()} (${os.arch()})`,
    displays,
  };
}

function startServer(): Promise<{ url: string; close: () => Promise<void> }> {
  const html = readFileSync(path.join(here, "index.html"));
  const server = createServer((req, res) => {
    if (req.url === "/" || req.url === "/index.html") {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end(html);
    } else {
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

async function measure(
  browser: Browser,
  url: string,
  technique: Technique,
  optimized: boolean,
): Promise<RunResult> {
  const context = await browser.newContext({ viewport: VIEWPORT });
  try {
    const page = await context.newPage();
    await page.goto(url);
    await page.waitForFunction(() => (window as unknown as { __spikeReady?: boolean }).__spikeReady);
    await page.bringToFront();
    return await page.evaluate(
      ([t, o]) =>
        (
          window as unknown as {
            __runSpike: (t: string, o: boolean) => Promise<RunResult>;
          }
        ).__runSpike(t, o),
      [technique, optimized] as const,
    );
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

const passes = (r: RunResult): boolean =>
  r.median_fps >= THRESHOLDS.median_fps && r.p95_frame_ms <= THRESHOLDS.p95_frame_ms;

async function main(): Promise<void> {
  const server = await startServer();
  const runs: (RunResult & { mode: Mode; browser: string })[] = [];
  try {
    for (const mode of ["headed", "headless"] as Mode[]) {
      const browser = await launch(mode);
      if (browser === null) continue;
      try {
        const name = `${browser.browserType().name()} ${browser.version()}`;
        for (const technique of TECHNIQUES) {
          const result = await measure(browser, server.url, technique, false);
          runs.push({ ...result, mode, browser: name });
          console.log(
            `${mode.padEnd(8)} ${technique.padEnd(22)} median_fps=${result.median_fps} ` +
              `p95_frame_ms=${result.p95_frame_ms} frames=${result.frames}`,
          );
        }
        const primaryHere = runs.find((r) => r.mode === mode && r.technique === PRIMARY_TECHNIQUE);
        // Extra labeled run with one known SVG optimization, only when the technique fails.
        if (primaryHere !== undefined && !passes(primaryHere)) {
          const result = await measure(browser, server.url, PRIMARY_TECHNIQUE, true);
          runs.push({ ...result, mode, browser: name });
          console.log(`${mode.padEnd(8)} ${PRIMARY_TECHNIQUE} (optimized) median_fps=${result.median_fps}`);
        }
      } finally {
        await browser.close();
      }
    }
  } finally {
    await server.close();
  }

  const primary =
    runs.find((r) => r.mode === "headed" && r.technique === PRIMARY_TECHNIQUE && !r.optimized) ??
    runs.find((r) => r.technique === PRIMARY_TECHNIQUE && !r.optimized);
  if (primary === undefined) throw new Error("No measurement run completed");

  const output = {
    median_fps: primary.median_fps,
    p95_frame_ms: primary.p95_frame_ms,
    entities: primary.entities,
    browser: primary.browser,
    hardware: hardwareInfo(),
    duration_s: primary.duration_s,
    frames: primary.frames,
    technique: primary.technique,
    mode: primary.mode,
    date: new Date().toISOString(),
    seed: primary.seed,
    refresh_hz_probe: primary.refresh_hz_probe,
    thresholds: THRESHOLDS,
    passes: passes(primary),
    decision: passes(primary) ? "keep-svg" : "recommend-konva-h2-blocked",
    runs,
  };
  const target = path.join(here, "result.json");
  writeFileSync(target, `${JSON.stringify(output, null, 2)}\n`);
  console.log(
    `Primary (${primary.mode}, ${primary.technique}): median_fps=${primary.median_fps} ` +
      `p95_frame_ms=${primary.p95_frame_ms} -> ${output.decision}. Wrote ${target}`,
  );
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
