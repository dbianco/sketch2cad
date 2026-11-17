// Fail on high-severity npm advisories that are not excepted (jotdown H0-8).
//
// Usage (from sketch2cad-web/):
//
//   pnpm audit --audit-level=high --json | node scripts/check-audit.mjs
//   node scripts/check-audit.mjs --input audit.json
//
// Input: the JSON written by `pnpm audit --json`, an object with an `advisories` map (id ->
// { id, github_advisory_id, cves, module_name, severity, findings, patched_versions, ... }) and
// `metadata.vulnerabilities` counts per severity. With --audit-level=high pnpm drops the
// advisories below high from `advisories` but keeps every severity in the counts.
//
// Rules (same semantics as sketch2cad-api/scripts/check_audit.py, fail closed):
//
// * HIGH, CRITICAL and UNKNOWN-severity advisories fail unless an exception in
//   audit-exceptions.json matches them: same package and an `id` equal to the GHSA id, the
//   numeric advisory id or one of its CVEs.
// * LOW, MODERATE and INFO advisories (and the counts of those hidden by --audit-level) only
//   print a warning.
// * Exceptions need non-empty `id`, `package`, `justification`, `status` (one of STATUSES) and an
//   `expires` date (YYYY-MM-DD). Expired exceptions fail and no longer cover their advisory;
//   exceptions that match no current advisory are stale and fail.
// * Input that is not pnpm audit JSON (for example empty output after a registry error) fails,
//   and so does a report whose high/critical counts are non-zero while it lists no high or
//   critical advisory (for example advisories hidden by pnpm's auditConfig.ignoreGhsas).

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const EXCEPTIONS_PATH = path.join(WEB_DIR, "audit-exceptions.json");
export const STATUSES = ["accepted-risk", "pending-owner-decision"];
const BLOCKING = new Set(["high", "critical", "unknown"]);
const SEVERITIES = new Set(["info", "low", "moderate", "high", "critical"]);
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const normalizeName = (name) => name.trim().toLowerCase();

/** Map a pnpm severity label to info, low, moderate, high, critical or unknown. */
export function parseSeverity(raw) {
  const text = typeof raw === "string" ? raw.trim().toLowerCase() : "";
  if (text === "medium") {
    return "moderate";
  }
  return SEVERITIES.has(text) ? text : "unknown";
}

const strings = (value) => (Array.isArray(value) ? value.map(String) : []);

function versions(findings) {
  const found = Array.isArray(findings) ? findings.map((item) => String(item?.version ?? "?")) : [];
  return [...new Set(found)];
}

/** Parse `pnpm audit --json` output; throw when it is not a pnpm audit report. */
export function parseReport(payload) {
  let data;
  try {
    data = JSON.parse(payload);
  } catch (error) {
    throw new Error(`input is not valid JSON (${error instanceof Error ? error.message : String(error)})`);
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("input is not pnpm audit JSON: expected an object");
  }
  const { advisories, metadata } = data;
  if (advisories === null || typeof advisories !== "object" || Array.isArray(advisories)) {
    throw new Error("input is not pnpm audit JSON: missing 'advisories' object");
  }
  const counts = metadata?.vulnerabilities;
  if (counts === null || typeof counts !== "object") {
    throw new Error("input is not pnpm audit JSON: missing 'metadata.vulnerabilities'");
  }
  const findings = Object.entries(advisories).map(([key, raw]) => {
    const entry = raw !== null && typeof raw === "object" ? raw : {};
    return {
      package: String(entry.module_name ?? "?"),
      versions: versions(entry.findings),
      id: String(entry.github_advisory_id ?? entry.id ?? key),
      aliases: [String(entry.id ?? key), ...strings(entry.cves)],
      severity: parseSeverity(entry.severity),
      patched: String(entry.patched_versions ?? "unknown"),
    };
  });
  const vulnerabilities = {};
  for (const severity of SEVERITIES) {
    const value = Number(counts[severity] ?? 0);
    vulnerabilities[severity] = Number.isFinite(value) ? value : 0;
  }
  return { findings, vulnerabilities };
}

/** Validate the exception entries; return the valid ones and the problems found. */
export function parseExceptions(data) {
  const raw = data?.exceptions;
  if (!Array.isArray(raw)) {
    return { exceptions: [], errors: ["audit-exceptions.json: 'exceptions' must be an array"] };
  }
  const exceptions = [];
  const errors = [];
  raw.forEach((entry, index) => {
    const label = `exception #${index + 1}`;
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
      errors.push(`${label}: must be an object`);
      return;
    }
    const fields = {};
    for (const key of ["id", "package", "justification", "status", "expires"]) {
      fields[key] = typeof entry[key] === "string" ? entry[key].trim() : "";
    }
    const problems = ["id", "package", "justification", "status"]
      .filter((key) => !fields[key])
      .map((key) => `missing or empty '${key}'`);
    if (fields.status && !STATUSES.includes(fields.status)) {
      problems.push(`status must be one of ${STATUSES.join(", ")}`);
    }
    if (!DATE_PATTERN.test(fields.expires) || Number.isNaN(Date.parse(fields.expires))) {
      problems.push("missing or invalid 'expires' date (YYYY-MM-DD)");
    }
    if (problems.length > 0) {
      errors.push(`${label} (${fields.id || "?"}): ${problems.join("; ")}`);
      return;
    }
    exceptions.push(fields);
  });
  return { exceptions, errors };
}

function matches(exception, finding) {
  return (
    normalizeName(exception.package) === normalizeName(finding.package) &&
    (exception.id === finding.id || finding.aliases.includes(exception.id))
  );
}

function describe(finding) {
  const aliases = finding.aliases.filter((alias) => alias !== finding.id);
  const extra = aliases.length > 0 ? ` (${aliases.join(", ")})` : "";
  return (
    `${finding.package} ${finding.versions.join(", ") || "?"}: ${finding.id}${extra} ` +
    `severity ${finding.severity}, patched versions: ${finding.patched}`
  );
}

/** Apply the severity policy and the exceptions; `today` is an ISO date (YYYY-MM-DD). */
export function evaluate(report, exceptions, today, exceptionErrors = []) {
  const errors = [...exceptionErrors];
  const warnings = [];
  const used = new Set();
  const isValid = (exception) => exception.expires >= today;
  for (const exception of exceptions) {
    if (!isValid(exception)) {
      errors.push(`expired exception: ${exception.package} ${exception.id} expired on ${exception.expires}`);
    }
  }
  for (const finding of report.findings) {
    const matching = exceptions.map((_, index) => index).filter((index) => matches(exceptions[index], finding));
    matching.forEach((index) => used.add(index));
    const valid = matching.filter((index) => isValid(exceptions[index]));
    if (!BLOCKING.has(finding.severity)) {
      warnings.push(describe(finding));
    } else if (valid.length === 0) {
      errors.push(`${describe(finding)} (no valid exception in audit-exceptions.json)`);
    } else {
      warnings.push(`${describe(finding)} (excepted: ${exceptions[valid[0]].status})`);
    }
  }
  const listed = (severity) => report.findings.filter((finding) => finding.severity === severity).length;
  for (const severity of ["high", "critical"]) {
    if (report.vulnerabilities[severity] > 0 && listed(severity) === 0) {
      errors.push(
        `the report counts ${report.vulnerabilities[severity]} ${severity} vulnerabilities but lists ` +
          `no ${severity} advisory (hidden by pnpm settings such as auditConfig.ignoreGhsas?)`,
      );
    }
  }
  for (const severity of ["info", "low", "moderate"]) {
    const hidden = report.vulnerabilities[severity] - listed(severity);
    if (hidden > 0) {
      warnings.push(`${hidden} ${severity} vulnerabilities not listed (below --audit-level)`);
    }
  }
  exceptions.forEach((exception, index) => {
    if (!used.has(index)) {
      errors.push(`stale exception: ${exception.package} ${exception.id} matches no current pnpm audit advisory`);
    }
  });
  return { errors, warnings };
}

export function main(argv = process.argv.slice(2)) {
  const { values } = parseArgs({
    args: argv,
    options: {
      input: { type: "string" },
      exceptions: { type: "string", default: EXCEPTIONS_PATH },
      today: { type: "string", default: new Date().toISOString().slice(0, 10) },
    },
  });
  const payload = readFileSync(values.input ?? 0, "utf8");
  let report;
  try {
    report = parseReport(payload);
  } catch (error) {
    console.log(`Audit check failed: ${error instanceof Error ? error.message : String(error)}`);
    return 1;
  }
  const { exceptions, errors } = parseExceptions(JSON.parse(readFileSync(values.exceptions, "utf8")));
  const result = evaluate(report, exceptions, values.today, errors);

  console.log(`Checked ${report.findings.length} advisories`);
  for (const warning of result.warnings) {
    console.log(`WARNING ${warning}`);
  }
  if (result.errors.length > 0) {
    console.log(`Audit check failed (${result.errors.length} problems):`);
    for (const error of result.errors) {
      console.log(`  ${error}`);
    }
    return 1;
  }
  console.log("Audit check passed");
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(`Audit check failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
