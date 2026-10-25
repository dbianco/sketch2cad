---
id: 001-020
title: "Add the license allow-list check for the API repository"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-001]
implements: [INFRA, REQ-16]
files: ["sketch2cad-api/licenses-allowed.txt", "sketch2cad-api/license-exceptions.toml", "sketch2cad-api/scripts/check_licenses.py", "sketch2cad-api/tests/test_check_licenses.py"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, python]
---

## Description

`check_licenses.py` runs `pip-licenses --format=json` and fails for any package whose license is outside MIT, BSD, Apache-2.0, PSF or LGPL unless listed in `license-exceptions.toml` with package, version and reason (decision D-6 lists psycopg). Unit tests feed synthetic license JSON. Run commands from the `sketch2cad-api/` folder.

Implements: INFRA, REQ-16

## Acceptance Criteria

- [ ] `uv run python scripts/check_licenses.py` exits 0 on the current lockfile.
- [ ] `uv run pytest tests/test_check_licenses.py -q` passes, including a case with a GPL-3.0 package that exits non-zero naming the package.
- [ ] `rg -n 'psycopg' license-exceptions.toml` returns the LGPL exception with a reason.
