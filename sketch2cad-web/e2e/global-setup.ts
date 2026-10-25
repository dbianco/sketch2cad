import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

/**
 * Bring the compose stack up with scripts/e2e-stack.sh (decision D-9) unless E2E_SKIP_STACK=1.
 *
 * The script writes the E2E user's credentials to a private temp directory outside the
 * repository; its path reaches the tests through E2E_CREDENTIALS_FILE (workers inherit the
 * environment of this process). With E2E_SKIP_STACK=1 the caller must provide that variable.
 */
export default function globalSetup(): void {
  if (process.env.E2E_SKIP_STACK === "1") {
    const credentials = process.env.E2E_CREDENTIALS_FILE;
    if (credentials === undefined || !existsSync(credentials)) {
      throw new Error("E2E_SKIP_STACK=1 requires E2E_CREDENTIALS_FILE to point to an existing file");
    }
    return;
  }
  const stateDir = mkdtempSync(path.join(os.tmpdir(), "sketch2cad-e2e-"));
  try {
    execFileSync("bash", [path.join(import.meta.dirname, "../scripts/e2e-stack.sh"), "up"], {
      env: { ...process.env, E2E_STATE_DIR: stateDir },
      stdio: "inherit",
    });
  } catch (error) {
    // The script has already removed the stack (unless KEEP_UP=1); drop the temp directory.
    rmSync(stateDir, { recursive: true, force: true });
    throw error;
  }
  process.env.E2E_STATE_DIR = stateDir;
  process.env.E2E_CREDENTIALS_FILE = path.join(stateDir, "credentials");
}
