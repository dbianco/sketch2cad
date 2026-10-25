import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import path from "node:path";

/** Remove the stack started by global-setup.ts (down -v) unless KEEP_UP=1 or E2E_SKIP_STACK=1. */
export default function globalTeardown(): void {
  if (process.env.E2E_SKIP_STACK === "1") {
    return;
  }
  const stateDir = process.env.E2E_STATE_DIR;
  if (process.env.KEEP_UP === "1") {
    console.log(
      `KEEP_UP=1: stack left running; rerun with E2E_SKIP_STACK=1 E2E_CREDENTIALS_FILE=${process.env.E2E_CREDENTIALS_FILE ?? ""}` +
        " and remove it with: bash scripts/e2e-stack.sh down",
    );
    return;
  }
  try {
    execFileSync("bash", [path.join(import.meta.dirname, "../scripts/e2e-stack.sh"), "down"], {
      stdio: "inherit",
    });
  } finally {
    if (stateDir !== undefined) {
      rmSync(stateDir, { recursive: true, force: true });
    }
  }
}
