---
id: 001-011
title: "Add the create-user CLI"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-007]
implements: [REQ-01]
files: ["sketch2cad-api/src/sketch2cad/cli.py", "sketch2cad-api/tests/test_cli_create_user.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, python]
---

## Description

`python -m sketch2cad.cli create-user --username X --role admin|technician|reviewer` reads the password from stdin (never from argv), hashes it and inserts the user. No default user exists. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01

## Acceptance Criteria

- [ ] Running the command with a password on stdin creates a `user` row whose `password_hash` starts with `$argon2id$` (`tests/test_cli_create_user.py`).
- [ ] Passing `--password` on the command line exits with status 2 and an error message.
- [ ] A duplicate username exits with status 1 and does not modify the table.
