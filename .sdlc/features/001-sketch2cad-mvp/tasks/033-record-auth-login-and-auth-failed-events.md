---
id: 001-033
title: "Record auth.login and auth.failed events"
status: todo
milestone: "H1 — Expedientes e ingesta"
repository: sketch2cad
depends_on: [001-030, 001-008]
implements: [REQ-11, REQ-01]
files: ["sketch2cad-api/src/sketch2cad/api/routers/auth.py", "sketch2cad-api/src/sketch2cad/auth/sessions.py", "sketch2cad-api/tests/auth/test_auth_events.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, python]
---

## Description

Failed and successful logins have no case; add a global chain (`case_file_id` nullable, chain keyed by `SHA-256('auth')`) or a separate `auth_event` stream using the same canonical hashing. Decide in the PR which and document it in `docs/audit.md`; the criteria below hold either way. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-11, REQ-01

## Acceptance Criteria

- [ ] A wrong password creates one `auth.failed` event and a correct one creates one `auth.login` event (`tests/auth/test_auth_events.py`).
- [ ] No event body contains the password or the session token: `rg -n 'password|token' ` over the serialized events in the test returns no values.
- [ ] The auth event stream verifies with `verify_chain`.
