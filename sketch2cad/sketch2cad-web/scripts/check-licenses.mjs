// Fail when an installed package has a license outside the allow-list (decision DEC-6).
//
// Usage (from sketch2cad-web/):
//
//   node scripts/check-licenses.mjs                   # inspect node_modules (prod + dev)
//   node scripts/check-licenses.mjs --input lic.json  # inspect a saved `license-checker --json` output
//
// Rules (same semantics as sketch2cad-api/scripts/check_licenses.py):
//
// * Licenses listed in licenses-allowed.json (MIT, BSD, Apache-2.0, PSF) pass automatically.
// * GPL and AGPL licenses are forbidden; no exception can allow them.
// * Any other license (LGPL, ISC, MIT-0, UNKNOWN, ...) passes only through a matching entry in
//   license-exceptions.json (same package, same version or "*", same license) that has a
//   non-empty reason. Entries with status "pending-owner-decision" pass with a warning.
// * Exceptions that match no installed package are stale and fail the check.
//
// License strings are SPDX expressions as reported by license-checker: OR takes the best
// alternative, AND the worst, "X WITH Y" is judged by X. A trailing "*" marks a license that
// license-checker guessed from a license file because package.json declares none; such a
// guess (e.g. "MIT*") is never allowed automatically and needs an exception.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ALLOWED_PATH = path.join(WEB_DIR, "licenses-allowed.json");
const EXCEPTIONS_PATH = path.join(WEB_DIR, "license-exceptions.json");
const PACKAGE_JSON_PATH = path.join(WEB_DIR, "package.json");
const LICENSE_CHECKER = "license-checker-rseidelsohn";

export const STATUSES = ["approved", "pending-owner-decision"];
const EXCEPTION_FIELDS = ["package", "version", "license", "reason", "status"];
const RANK = { forbidden: 0, "needs-exception": 1, allowed: 2 };

// Lower-cased license spellings mapped to a normalized SPDX-like identifier.
const ALIASES = new Map([
  ["mit", "MIT"],
  ["mit license", "MIT"],
  ["the mit license", "MIT"],
  ["mit-0", "MIT-0"],
  ["bsd", "BSD"],
  ["bsd license", "BSD"],
  ["bsd-2-clause", "BSD-2-Clause"],
  ["bsd 2-clause license", "BSD-2-Clause"],
  ["simplified bsd", "BSD-2-Clause"],
  ["bsd-3-clause", "BSD-3-Clause"],
  ["bsd 3-clause license", "BSD-3-Clause"],
  ["new bsd license", "BSD-3-Clause"],
  ["apache-2.0", "Apache-2.0"],
  ["apache 2.0", "Apache-2.0"],
  ["apache license 2.0", "Apache-2.0"],
  ["apache license, version 2.0", "Apache-2.0"],
  ["apache license version 2.0", "Apache-2.0"],
  ["psf", "PSF-2.0"],
  ["psf-2.0", "PSF-2.0"],
  ["python-2.0", "PSF-2.0"],
  ["lgplv3", "LGPL-3.0"],
  ["lgplv3+", "LGPL-3.0-or-later"],
  ["gplv2", "GPL-2.0"],
  ["gplv3", "GPL-3.0"],
  ["agplv3", "AGPL-3.0"],
  ["unknown", "UNKNOWN"],
  ["", "UNKNOWN"],
]);

const FORBIDDEN = /^A?GPL\b|^(gnu )?(affero )?general public license/i;
const TOKEN = /\(|\)|[^()]+/g;

/** Normalize a single license name; unknown spellings are returned trimmed. */
export function normalizeTerm(term) {
  const cleaned = term.split(/\s+/).filter(Boolean).join(" ");
  return ALIASES.get(cleaned.toLowerCase()) ?? cleaned;
}

/** Split a license string into terms, operators (AND/OR) and parentheses. */
export function tokenize(raw) {
  const out = [];
  for (const part of raw.split(";")) {
    if (out.length > 0) {
      out.push("AND");
    }
    for (const chunk of part.match(TOKEN) ?? []) {
      if (chunk === "(" || chunk === ")") {
        out.push(chunk);
        continue;
      }
      for (const piece of ` ${chunk} `.split(/\s+(AND|OR)\s+/)) {
        const trimmed = piece.trim();
        if (trimmed === "AND" || trimmed === "OR") {
          out.push(trimmed);
        } else if (trimmed) {
          // "Apache-2.0 WITH LLVM-exception" is judged by its base license.
          out.push(normalizeTerm(trimmed.split(/\s+WITH\s+/)[0]));
        }
      }
    }
  }
  return out.length > 0 ? out : ["UNKNOWN"];
}

/** Return the canonical form of a license, e.g. "(MIT License OR ISC)" -> "(MIT OR ISC)". */
export function normalizeLicense(raw) {
  return tokenize(raw).join(" ").replaceAll("( ", "(").replaceAll(" )", ")");
}

export function classifyTerm(term, allowed) {
  if (allowed.has(term)) {
    return "allowed";
  }
  if (FORBIDDEN.test(term)) {
    return "forbidden";
  }
  return "needs-exception";
}

const best = (a, b) => (RANK[b] > RANK[a] ? b : a);
const worst = (a, b) => (RANK[b] < RANK[a] ? b : a);

/** Classify a license string: OR takes the best alternative, AND the worst. */
export function classify(raw, allowed) {
  const tokens = tokenize(raw);
  let position = 0;

  const parseOr = () => {
    let verdict = parseAnd();
    while (tokens[position] === "OR") {
      position += 1;
      verdict = best(verdict, parseAnd());
    }
    return verdict;
  };

  const parseAnd = () => {
    let verdict = parseAtom();
    while (tokens[position] === "AND") {
      position += 1;
      verdict = worst(verdict, parseAtom());
    }
    return verdict;
  };

  const parseAtom = () => {
    if (position >= tokens.length) {
      return "needs-exception";
    }
    const current = tokens[position];
    position += 1;
    if (current === "(") {
      const verdict = parseOr();
      if (tokens[position] === ")") {
        position += 1;
      }
      return verdict;
    }
    if (current === "AND" || current === "OR" || current === ")") {
      return "needs-exception";
    }
    return classifyTerm(current, allowed);
  };

  const verdict = parseOr();
  return position === tokens.length ? verdict : worst(verdict, "needs-exception");
}

/** Read the allow-list: `{"licenses": ["MIT", ...]}`. */
export function parseAllowed(data) {
  if (!Array.isArray(data?.licenses) || !data.licenses.every((id) => typeof id === "string")) {
    throw new Error("licenses-allowed.json: 'licenses' must be an array of strings");
  }
  return new Set(data.licenses.map((id) => id.trim()).filter(Boolean));
}

/** Validate the `exceptions` entries; return the valid entries and the problems found. */
export function parseExceptions(data) {
  const rawEntries = data?.exceptions ?? [];
  if (!Array.isArray(rawEntries)) {
    return { exceptions: [], errors: ["license-exceptions.json: 'exceptions' must be an array of objects"] };
  }
  const exceptions = [];
  const errors = [];
  rawEntries.forEach((raw, offset) => {
    const index = offset + 1;
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
      errors.push(`exception #${index}: must be an object`);
      return;
    }
    const fields = {};
    for (const key of EXCEPTION_FIELDS) {
      fields[key] = typeof raw[key] === "string" ? raw[key].trim() : "";
    }
    const label = `exception #${index} (${fields.package || "?"})`;
    const problems = EXCEPTION_FIELDS.filter((key) => !fields[key]).map((key) => `missing or empty '${key}'`);
    if (fields.status && !STATUSES.includes(fields.status)) {
      problems.push(`status must be one of ${STATUSES.join(", ")}`);
    }
    if (problems.length > 0) {
      errors.push(`${label}: ${problems.join("; ")}`);
      return;
    }
    exceptions.push(fields);
  });
  return { exceptions, errors };
}

const normalizeName = (name) => name.trim().toLowerCase();

function matches(exception, pkg) {
  return (
    normalizeName(exception.package) === normalizeName(pkg.name) &&
    (exception.version === "*" || exception.version === pkg.version) &&
    normalizeLicense(exception.license) === normalizeLicense(pkg.license)
  );
}

/** Check every package against the allow-list and the exceptions. */
export function evaluate(packages, allowed, exceptions, exceptionErrors = []) {
  const errors = [...exceptionErrors];
  const warnings = [];
  const used = new Set();
  const sorted = [...packages].sort((a, b) => normalizeName(a.name).localeCompare(normalizeName(b.name)));
  for (const pkg of sorted) {
    const verdict = classify(pkg.license, allowed);
    if (verdict === "allowed") {
      continue;
    }
    const label = `${pkg.name} ${pkg.version}: ${pkg.license}`;
    const match = exceptions.findIndex((exception) => matches(exception, pkg));
    if (match !== -1) {
      used.add(match);
    }
    if (verdict === "forbidden") {
      errors.push(`${label} (GPL/AGPL is forbidden; exceptions cannot allow it)`);
    } else if (match === -1) {
      errors.push(`${label} (not in the allow-list and no matching exception)`);
    } else if (exceptions[match].status !== "approved") {
      warnings.push(`${label} (exception ${exceptions[match].status})`);
    }
  }
  exceptions.forEach((exception, index) => {
    if (!used.has(index)) {
      errors.push(
        `stale exception: ${exception.package} ${exception.version} (${exception.license}) ` +
          "matches no installed package that needs it",
      );
    }
  });
  return { errors, warnings, checked: packages.length };
}

function licenseString(licenses) {
  if (Array.isArray(licenses)) {
    // Several declared licenses must all be honoured, so they are joined with AND.
    return licenses.length > 0 ? licenses.map((item) => `(${String(item)})`).join(" AND ") : "UNKNOWN";
  }
  return typeof licenses === "string" && licenses.trim() ? licenses.trim() : "UNKNOWN";
}

/** Parse `license-checker --json` output (keys are "name@version"), ignoring names in `skip`. */
export function parsePackages(payload, skip = new Set()) {
  const items = JSON.parse(payload);
  const packages = [];
  for (const [key, item] of Object.entries(items)) {
    const at = key.lastIndexOf("@");
    const name = at > 0 ? key.slice(0, at) : key;
    const version = at > 0 ? key.slice(at + 1) : "";
    if (skip.has(normalizeName(name))) {
      continue;
    }
    packages.push({ name, version, license: licenseString(item?.licenses) });
  }
  return packages;
}

/** Run license-checker over the production and development dependencies of the project. */
export function runLicenseChecker() {
  const require = createRequire(import.meta.url);
  const manifestPath = require.resolve(`${LICENSE_CHECKER}/package.json`);
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const bin = typeof manifest.bin === "string" ? manifest.bin : manifest.bin[LICENSE_CHECKER];
  return execFileSync(
    process.execPath,
    [path.join(path.dirname(manifestPath), bin), "--json", "--excludePrivatePackages", "--start", WEB_DIR],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "inherit"] },
  );
}

const readJson = (file) => JSON.parse(readFileSync(file, "utf8"));

export function main(argv = process.argv.slice(2)) {
  const { values } = parseArgs({
    args: argv,
    options: {
      input: { type: "string" },
      allowed: { type: "string", default: ALLOWED_PATH },
      exceptions: { type: "string", default: EXCEPTIONS_PATH },
    },
  });
  const payload = values.input ? readFileSync(values.input, "utf8") : runLicenseChecker();
  const own = normalizeName(readJson(PACKAGE_JSON_PATH).name);
  const packages = parsePackages(payload, new Set([own]));
  const { exceptions, errors } = parseExceptions(readJson(values.exceptions));
  const report = evaluate(packages, parseAllowed(readJson(values.allowed)), exceptions, errors);

  console.log(`Checked ${report.checked} packages (skipped the project itself: ${own})`);
  for (const warning of report.warnings) {
    console.log(`WARNING ${warning}`);
  }
  if (report.errors.length > 0) {
    console.log(`License check failed (${report.errors.length} problems):`);
    for (const error of report.errors) {
      console.log(`  ${error}`);
    }
    return 1;
  }
  console.log("License check passed");
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(`License check failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
