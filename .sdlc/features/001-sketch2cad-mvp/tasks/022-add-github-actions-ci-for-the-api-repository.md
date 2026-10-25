---
id: 001-022
title: "Add GitHub Actions CI for the API repository"
status: partial
unmet: ["GPL-3.0 branch makes the licenses job fail, run URL recorded (needs GitHub)", "run summary shows ci_duration_minutes in a real workflow run (needs GitHub)"]
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-005, 001-020, 001-014]
implements: [INFRA, REQ-16]
files: [".github/workflows/api-ci.yml", "sketch2cad-api/audit-exceptions.toml", "sketch2cad-api/.gitleaks.toml", "sketch2cad-api/scripts/check_audit.py", "sketch2cad-api/tests/test_check_audit.py", "sketch2cad-api/tests/fixtures/fake_secret.txt"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, python]
---

## Description

Workflow `api-ci.yml` at the repository root with `paths: sketch2cad-api/**` and `working-directory: sketch2cad-api`. Jobs: lint (ruff, pyright), test (pytest with a PostgreSQL 16 service), migrations (`scripts/migration_roundtrip.sh`), licenses (`check_licenses.py`), audit (`pip-audit` filtered by `check_audit.py`: fail on high severity unless listed in `audit-exceptions.toml` with justification), secrets (`gitleaks`, with `tests/fixtures/` excluded in normal scans) and compose smoke. Each run writes its duration to the job summary (H0-6, report only). Covers H0-7, H0-8, H0-9. Run commands from the `sketch2cad-api/` folder.

Implements: INFRA, REQ-16

## Acceptance Criteria

- [ ] Pushing a branch that adds a GPL-3.0 dependency makes the `licenses` job fail (verified once; run URL recorded in the PR).
- [ ] `uv run pytest tests/test_check_audit.py -q` passes: a high-severity finding without an exception fails; the same finding listed with a justification passes.
- [ ] `gitleaks detect --no-git --source tests/fixtures --config .gitleaks.toml` reports the fake key in `fake_secret.txt`, and the repository scan (`gitleaks detect --source .`) with the repository config exits 0.
- [ ] The workflow run summary contains a line `ci_duration_minutes=<n>`.
