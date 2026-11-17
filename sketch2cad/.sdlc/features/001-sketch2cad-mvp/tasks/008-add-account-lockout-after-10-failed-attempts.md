---
id: 001-008
title: "Add account lockout after 10 failed attempts"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-007]
implements: [REQ-01]
files: ["sketch2cad-api/src/sketch2cad/auth/lockout.py", "sketch2cad-api/src/sketch2cad/api/routers/auth.py", "sketch2cad-api/tests/auth/test_lockout.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 4, pessimistic: 7 }
labels: [ai-sdlc, python]
---

## Description

Increment `failed_attempts` on each wrong password; at 10 set `locked_until = now + auth.lockout_minutes` (default 15) and return 423 with `locked_until` in the problem body, even when the password is then correct. A successful login after the lock expires resets the counter. Use an injectable clock. Covers H0-3. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01

## Acceptance Criteria

- [ ] After 10 wrong passwords, the 11th attempt with the correct password returns 423 (`tests/auth/test_lockout.py`).
- [ ] With the injected clock advanced by 16 minutes, the same login returns 200 and `failed_attempts` is 0.
- [ ] A successful login before the 10th failure resets `failed_attempts` to 0.
