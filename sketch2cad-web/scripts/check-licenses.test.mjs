// Tests for scripts/check-licenses.mjs. Run with: node --test scripts/check-licenses.test.mjs
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  classify,
  evaluate,
  main,
  normalizeLicense,
  parseAllowed,
  parseExceptions,
  parsePackages,
} from "./check-licenses.mjs";

const SCRIPT = fileURLToPath(new URL("./check-licenses.mjs", import.meta.url));
const WEB_DIR = path.resolve(path.dirname(SCRIPT), "..");
const readJson = (file) => JSON.parse(readFileSync(path.join(WEB_DIR, file), "utf8"));
const ALLOWED = parseAllowed(readJson("licenses-allowed.json"));

const tmp = mkdtempSync(path.join(tmpdir(), "check-licenses-"));
after(() => rmSync(tmp, { recursive: true, force: true }));

function exception(overrides = {}) {
  return {
    package: "lgpl-lib",
    version: "1.0.0",
    license: "LGPL-3.0-only",
    reason: "Documented in DEC-6.",
    status: "approved",
    ...overrides,
  };
}

const pkg = (name, version, license) => ({ name, version, license });

function writeFixtures(name, licenses, exceptions) {
  const input = path.join(tmp, `${name}-input.json`);
  const exceptionsPath = path.join(tmp, `${name}-exceptions.json`);
  writeFileSync(input, JSON.stringify(licenses));
  writeFileSync(exceptionsPath, JSON.stringify({ exceptions }));
  return ["--input", input, "--exceptions", exceptionsPath];
}

function runScript(args) {
  return spawnSync(process.execPath, [SCRIPT, ...args], { cwd: WEB_DIR, encoding: "utf8" });
}

describe("allow-list file", () => {
  test("contains exactly the DEC-6 licenses", () => {
    assert.deepEqual([...ALLOWED].sort(), ["Apache-2.0", "BSD", "BSD-2-Clause", "BSD-3-Clause", "MIT", "PSF-2.0"]);
  });

  test("rejects a non-array licenses field", () => {
    assert.throws(() => parseAllowed({ licenses: "MIT" }), /must be an array/);
  });
});

describe("normalizeLicense", () => {
  test("normalizes spellings, operators and WITH clauses", () => {
    assert.equal(normalizeLicense("MIT License"), "MIT");
    assert.equal(normalizeLicense("(MIT  OR Apache 2.0)"), "(MIT OR Apache-2.0)");
    assert.equal(normalizeLicense("Apache-2.0 WITH LLVM-exception"), "Apache-2.0");
    assert.equal(normalizeLicense("MIT; ISC"), "MIT AND ISC");
    assert.equal(normalizeLicense(""), "UNKNOWN");
  });
});

describe("classify", () => {
  test("allows the allow-listed licenses and SPDX aliases", () => {
    for (const license of ["MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "Python-2.0", "PSF-2.0"]) {
      assert.equal(classify(license, ALLOWED), "allowed", license);
    }
  });

  test("OR takes the best option and AND the worst", () => {
    assert.equal(classify("(MIT OR CC0-1.0)", ALLOWED), "allowed");
    assert.equal(classify("(MIT OR GPL-3.0-only)", ALLOWED), "allowed");
    assert.equal(classify("(MIT AND CC-BY-3.0)", ALLOWED), "needs-exception");
    assert.equal(classify("MIT AND GPL-3.0-only", ALLOWED), "forbidden");
    assert.equal(classify("(ISC OR (MIT AND Apache-2.0))", ALLOWED), "allowed");
  });

  test("forbids GPL and AGPL in every spelling", () => {
    for (const license of ["GPL-3.0", "GPL-2.0-or-later", "AGPL-3.0-only", "GPLv3", "AGPLv3", "GPL*"]) {
      assert.equal(classify(license, ALLOWED), "forbidden", license);
    }
  });

  test("requires an exception for LGPL and every other license", () => {
    const others = ["LGPL-3.0-only", "LGPL-2.1-or-later", "ISC", "MIT-0", "0BSD", "MPL-2.0", "CC0-1.0"];
    for (const license of [...others, "Unlicense", "Public Domain", "UNKNOWN", "UNLICENSED", "BlueOak-1.0.0"]) {
      assert.equal(classify(license, ALLOWED), "needs-exception", license);
    }
  });

  test("treats a license guessed by license-checker (trailing *) as unknown", () => {
    assert.equal(classify("MIT*", ALLOWED), "needs-exception");
    assert.equal(classify("BSD*", ALLOWED), "needs-exception");
  });

  test("malformed expressions need an exception", () => {
    assert.equal(classify("MIT AND", ALLOWED), "needs-exception");
    assert.equal(classify("MIT )", ALLOWED), "needs-exception");
  });
});

describe("parseExceptions", () => {
  test("accepts a complete entry", () => {
    assert.deepEqual(parseExceptions({ exceptions: [exception()] }), { exceptions: [exception()], errors: [] });
  });

  test("requires a reason and every other field", () => {
    const { exceptions, errors } = parseExceptions({ exceptions: [exception({ reason: " " }), {}] });
    assert.deepEqual(exceptions, []);
    assert.match(errors[0], /exception #1 \(lgpl-lib\): missing or empty 'reason'/);
    assert.match(errors[1], /exception #2 \(\?\): missing or empty 'package'.*'status'/);
  });

  test("rejects an unknown status and a non-array container", () => {
    assert.match(parseExceptions({ exceptions: [exception({ status: "maybe" })] }).errors[0], /status must be one of/);
    assert.match(parseExceptions({ exceptions: {} }).errors[0], /must be an array/);
  });

  test("the committed exceptions file is valid and holds no GPL/AGPL entry", () => {
    const { exceptions, errors } = parseExceptions(readJson("license-exceptions.json"));
    assert.deepEqual(errors, []);
    for (const entry of exceptions) {
      assert.notEqual(classify(entry.license, ALLOWED), "forbidden", entry.package);
    }
  });
});

describe("evaluate", () => {
  test("LGPL needs a documented exception", () => {
    const packages = [pkg("lgpl-lib", "1.0.0", "LGPL-3.0-only")];
    assert.match(evaluate(packages, ALLOWED, []).errors[0], /lgpl-lib 1\.0\.0: LGPL-3\.0-only \(not in the allow-list/);
    assert.deepEqual(evaluate(packages, ALLOWED, [exception()]), { errors: [], warnings: [], checked: 1 });
  });

  test("a pending exception passes with a warning", () => {
    const report = evaluate([pkg("Lgpl-Lib", "1.0.0", "LGPL-3.0-only")], ALLOWED, [
      exception({ version: "*", status: "pending-owner-decision" }),
    ]);
    assert.deepEqual(report.errors, []);
    assert.deepEqual(report.warnings, ["Lgpl-Lib 1.0.0: LGPL-3.0-only (exception pending-owner-decision)"]);
  });

  test("an exception for another version or license does not match and is stale", () => {
    const packages = [pkg("lgpl-lib", "2.0.0", "LGPL-3.0-only")];
    const report = evaluate(packages, ALLOWED, [exception(), exception({ version: "*", license: "ISC" })]);
    assert.equal(report.errors.length, 3);
    assert.match(report.errors[0], /lgpl-lib 2\.0\.0/);
    assert.match(report.errors[1], /stale exception: lgpl-lib 1\.0\.0 \(LGPL-3\.0-only\)/);
    assert.match(report.errors[2], /stale exception: lgpl-lib \* \(ISC\)/);
  });

  test("an exception for an allowed package is stale", () => {
    const report = evaluate([pkg("mit-lib", "1.0.0", "MIT")], ALLOWED, [exception({ package: "mit-lib", license: "MIT" })]);
    assert.match(report.errors[0], /stale exception: mit-lib/);
  });

  test("AGPL is denied even with an exception", () => {
    const report = evaluate([pkg("agpl-lib", "1.0.0", "AGPL-3.0-only")], ALLOWED, [
      exception({ package: "agpl-lib", license: "AGPL-3.0-only" }),
    ]);
    assert.deepEqual(report.errors, ["agpl-lib 1.0.0: AGPL-3.0-only (GPL/AGPL is forbidden; exceptions cannot allow it)"]);
  });

  test("invalid exception entries are reported as errors", () => {
    assert.deepEqual(evaluate([], ALLOWED, [], ["bad entry"]).errors, ["bad entry"]);
  });
});

describe("parsePackages", () => {
  test("splits scoped names, joins license arrays with AND and skips the project", () => {
    const payload = JSON.stringify({
      "@scope/lib@1.2.3": { licenses: "MIT" },
      "multi@0.1.0": { licenses: ["MIT", "ISC"] },
      "bare@1.0.0": {},
      "sketch2cad-web@0.1.0": { licenses: "UNLICENSED" },
    });
    assert.deepEqual(parsePackages(payload, new Set(["sketch2cad-web"])), [
      pkg("@scope/lib", "1.2.3", "MIT"),
      pkg("multi", "0.1.0", "(MIT) AND (ISC)"),
      pkg("bare", "1.0.0", "UNKNOWN"),
    ]);
    assert.equal(classify("(MIT) AND (ISC)", ALLOWED), "needs-exception");
  });
});

describe("main", () => {
  test("passes with injected input and prints pending warnings", (t) => {
    const lines = [];
    t.mock.method(console, "log", (line) => lines.push(line));
    const args = writeFixtures("pass", { "ok@1.0.0": { licenses: "MIT" }, "lgpl-lib@1.0.0": { licenses: "LGPL-3.0-only" } }, [
      exception({ status: "pending-owner-decision" }),
    ]);
    assert.equal(main(args), 0);
    assert.deepEqual(lines, [
      "Checked 2 packages (skipped the project itself: sketch2cad-web)",
      "WARNING lgpl-lib 1.0.0: LGPL-3.0-only (exception pending-owner-decision)",
      "License check passed",
    ]);
  });

  test("a synthetic GPL-3.0 package makes the script exit non-zero naming the package", () => {
    const args = writeFixtures("gpl", { "ok@1.0.0": { licenses: "MIT" }, "evil-gpl@3.1.4": { licenses: "GPL-3.0" } }, []);
    const result = runScript(args);
    assert.equal(result.status, 1, result.stderr);
    assert.match(result.stdout, /License check failed \(1 problems\):/);
    assert.match(result.stdout, /evil-gpl 3\.1\.4: GPL-3\.0 \(GPL\/AGPL is forbidden/);
  });

  test("an exception without a reason makes the script fail", () => {
    const args = writeFixtures("reason", { "lgpl-lib@1.0.0": { licenses: "LGPL-3.0-only" } }, [exception({ reason: "" })]);
    const result = runScript(args);
    assert.equal(result.status, 1);
    assert.match(result.stdout, /exception #1 \(lgpl-lib\): missing or empty 'reason'/);
    assert.match(result.stdout, /lgpl-lib 1\.0\.0: LGPL-3\.0-only \(not in the allow-list/);
  });

  test("an unreadable input file fails with a message", () => {
    const result = runScript(["--input", path.join(tmp, "missing.json")]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /License check failed: ENOENT/);
  });
});
