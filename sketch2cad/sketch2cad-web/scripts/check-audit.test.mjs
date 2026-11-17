// Tests for scripts/check-audit.mjs. Run with: node --test scripts/check-audit.test.mjs
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { evaluate, parseExceptions, parseReport, parseSeverity } from "./check-audit.mjs";

const SCRIPT = fileURLToPath(new URL("./check-audit.mjs", import.meta.url));
const WEB_DIR = path.resolve(path.dirname(SCRIPT), "..");
const TODAY = "2026-06-01";

const tmp = mkdtempSync(path.join(tmpdir(), "check-audit-"));
after(() => rmSync(tmp, { recursive: true, force: true }));

// Synthetic advisory in the shape written by `pnpm audit --json`.
function advisory(overrides = {}) {
  return {
    id: 1100001,
    github_advisory_id: "GHSA-aaaa-bbbb-cccc",
    cves: ["CVE-2026-0001"],
    module_name: "vulnerable-lib",
    severity: "high",
    title: "Prototype pollution",
    vulnerable_versions: "<1.2.3",
    patched_versions: ">=1.2.3",
    findings: [{ version: "1.0.0", paths: ["vulnerable-lib"] }],
    ...overrides,
  };
}

function auditJson(advisories, counts = {}) {
  const vulnerabilities = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  for (const item of advisories) {
    vulnerabilities[item.severity] = (vulnerabilities[item.severity] ?? 0) + 1;
  }
  return JSON.stringify({
    advisories: Object.fromEntries(advisories.map((item) => [String(item.id), item])),
    metadata: { vulnerabilities: { ...vulnerabilities, ...counts }, totalDependencies: 10 },
  });
}

function exception(overrides = {}) {
  return {
    id: "GHSA-aaaa-bbbb-cccc",
    package: "vulnerable-lib",
    justification: "Only reachable from the dev server; tracked in the backlog.",
    expires: "2026-12-31",
    status: "accepted-risk",
    ...overrides,
  };
}

function check(advisories, exceptions = [], counts = {}) {
  const parsed = parseExceptions({ exceptions });
  return evaluate(parseReport(auditJson(advisories, counts)), parsed.exceptions, TODAY, parsed.errors);
}

function runScript(name, payload, exceptions) {
  const input = path.join(tmp, `${name}-audit.json`);
  const exceptionsPath = path.join(tmp, `${name}-exceptions.json`);
  writeFileSync(input, payload);
  writeFileSync(exceptionsPath, JSON.stringify({ exceptions }));
  return spawnSync(
    process.execPath,
    [SCRIPT, "--input", input, "--exceptions", exceptionsPath, "--today", TODAY],
    { cwd: WEB_DIR, encoding: "utf8" },
  );
}

describe("parseSeverity", () => {
  test("maps pnpm labels and treats anything else as unknown", () => {
    assert.equal(parseSeverity("HIGH"), "high");
    assert.equal(parseSeverity("medium"), "moderate");
    assert.equal(parseSeverity("critical"), "critical");
    assert.equal(parseSeverity("severe"), "unknown");
    assert.equal(parseSeverity(undefined), "unknown");
  });
});

describe("parseReport", () => {
  test("rejects input that is not pnpm audit JSON", () => {
    assert.throws(() => parseReport(""), /not valid JSON/);
    assert.throws(() => parseReport("[]"), /expected an object/);
    assert.throws(() => parseReport('{"dependencies": []}'), /missing 'advisories'/);
    assert.throws(() => parseReport('{"advisories": {}}'), /metadata.vulnerabilities/);
  });

  test("reads package, ids and versions of each advisory", () => {
    const [finding] = parseReport(auditJson([advisory()])).findings;
    assert.deepEqual(finding, {
      package: "vulnerable-lib",
      versions: ["1.0.0"],
      id: "GHSA-aaaa-bbbb-cccc",
      aliases: ["1100001", "CVE-2026-0001"],
      severity: "high",
      patched: ">=1.2.3",
    });
  });
});

describe("evaluate", () => {
  test("a high advisory without exception fails naming package and advisory id", () => {
    const { errors } = check([advisory()]);
    assert.equal(errors.length, 1);
    assert.match(errors[0], /vulnerable-lib 1\.0\.0: GHSA-aaaa-bbbb-cccc/);
    assert.match(errors[0], /no valid exception/);
  });

  test("critical and unknown severities also fail", () => {
    const critical = check([advisory({ severity: "critical" })]);
    const unknown = check([advisory({ severity: "bogus" })], [], { high: 0 });
    assert.equal(critical.errors.length, 1);
    assert.equal(unknown.errors.length, 1);
    assert.match(unknown.errors[0], /severity unknown/);
  });

  test("an exception with justification lets the high advisory pass", () => {
    const { errors, warnings } = check([advisory()], [exception()]);
    assert.deepEqual(errors, []);
    assert.match(warnings[0], /excepted: accepted-risk/);
  });

  test("an exception can match the CVE or the numeric advisory id", () => {
    assert.deepEqual(check([advisory()], [exception({ id: "CVE-2026-0001" })]).errors, []);
    assert.deepEqual(check([advisory()], [exception({ id: "1100001", status: "pending-owner-decision" })]).errors, []);
  });

  test("an exception for another package does not cover the advisory", () => {
    const { errors } = check([advisory()], [exception({ package: "other-lib" })]);
    assert.ok(errors.some((error) => /no valid exception/.test(error)));
    assert.ok(errors.some((error) => /stale exception: other-lib/.test(error)));
  });

  test("an expired exception fails and no longer covers the advisory", () => {
    const { errors } = check([advisory()], [exception({ expires: "2026-05-31" })]);
    assert.ok(errors.some((error) => /expired exception: vulnerable-lib GHSA-aaaa-bbbb-cccc expired on 2026-05-31/.test(error)));
    assert.ok(errors.some((error) => /no valid exception/.test(error)));
  });

  test("an exception expiring today is still valid", () => {
    assert.deepEqual(check([advisory()], [exception({ expires: TODAY })]).errors, []);
  });

  test("a stale exception fails", () => {
    const { errors } = check([], [exception()]);
    assert.deepEqual(errors, ["stale exception: vulnerable-lib GHSA-aaaa-bbbb-cccc matches no current pnpm audit advisory"]);
  });

  test("an empty justification, a bad status or a bad date fails", () => {
    const { errors } = check(
      [advisory()],
      [exception({ justification: "  " }), exception({ status: "approved" }), exception({ expires: "31/12/2026" })],
    );
    assert.match(errors[0], /exception #1 .*missing or empty 'justification'/);
    assert.match(errors[1], /exception #2 .*status must be one of/);
    assert.match(errors[2], /exception #3 .*invalid 'expires'/);
    assert.ok(errors.some((error) => /no valid exception/.test(error)));
  });

  test("moderate, low and info advisories only warn", () => {
    const { errors, warnings } = check([
      advisory({ id: 1, severity: "moderate", module_name: "a" }),
      advisory({ id: 2, severity: "low", module_name: "b" }),
      advisory({ id: 3, severity: "info", module_name: "c" }),
    ]);
    assert.deepEqual(errors, []);
    assert.equal(warnings.length, 3);
  });

  test("counts below --audit-level only warn", () => {
    const { errors, warnings } = check([], [], { moderate: 2 });
    assert.deepEqual(errors, []);
    assert.deepEqual(warnings, ["2 moderate vulnerabilities not listed (below --audit-level)"]);
  });

  test("high counts without any listed high advisory fail closed", () => {
    const { errors } = check([], [], { high: 1 });
    assert.match(errors[0], /counts 1 high vulnerabilities but lists no high advisory/);
  });
});

describe("audit-exceptions.json", () => {
  test("is a valid, empty exception list", () => {
    const data = JSON.parse(readFileSync(path.join(WEB_DIR, "audit-exceptions.json"), "utf8"));
    assert.deepEqual(parseExceptions(data), { exceptions: [], errors: [] });
  });
});

describe("command line", () => {
  test("exits 1 and names the advisory when a high advisory is not excepted", () => {
    const result = runScript("high", auditJson([advisory()]), []);
    assert.equal(result.status, 1);
    assert.match(result.stdout, /Audit check failed \(1 problems\)/);
    assert.match(result.stdout, /vulnerable-lib 1\.0\.0: GHSA-aaaa-bbbb-cccc/);
  });

  test("exits 0 when the high advisory is excepted", () => {
    const result = runScript("excepted", auditJson([advisory()]), [exception()]);
    assert.equal(result.status, 0, result.stdout);
    assert.match(result.stdout, /Audit check passed/);
  });

  test("exits 0 with a clean report", () => {
    const result = runScript("clean", auditJson([]), []);
    assert.equal(result.status, 0, result.stdout);
  });

  test("exits 1 on unparsable input", () => {
    const result = runScript("broken", "ERR_PNPM_AUDIT_BAD_RESPONSE", []);
    assert.equal(result.status, 1);
    assert.match(result.stdout, /Audit check failed: input is not valid JSON/);
  });

  test("exits 1 on empty input read from stdin", () => {
    const result = spawnSync(process.execPath, [SCRIPT], { cwd: WEB_DIR, encoding: "utf8", input: "" });
    assert.equal(result.status, 1);
    assert.match(result.stdout, /not valid JSON/);
  });
});
