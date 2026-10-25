---
id: 001-009
title: "Enforce CSRF tokens on every mutation"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-007]
implements: [REQ-01]
files: ["sketch2cad-api/src/sketch2cad/auth/csrf.py", "sketch2cad-api/src/sketch2cad/api/deps.py", "sketch2cad-api/tests/auth/test_csrf_cookies.py"]
parallelizable: false
estimate: { optimistic: 2, likely: 4, pessimistic: 6 }
labels: [ai-sdlc, python]
---

## Description

Dependency `require_csrf` compares header `X-CSRF-Token` with cookie `s2c_csrf` (constant-time) on POST, PUT, PATCH and DELETE routes; GET is exempt. Decision D-8: SameSite=Lax plus mandatory CSRF. Covers H0-13. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01

## Acceptance Criteria

- [ ] A POST, PUT, PATCH and DELETE with a valid session cookie and no `X-CSRF-Token` each return 403 (parametrized test in `tests/auth/test_csrf_cookies.py`).
- [ ] `GET /api/v1/auth/me` without the token returns 200.
- [ ] The `Set-Cookie` of `s2c_session` contains `HttpOnly` and `SameSite=Lax`; with `APP_ENV=prod` it also contains `Secure`.
- [ ] `rg -n 'router\.(post|put|patch|delete)' src/sketch2cad/api/routers | rg -v 'require_csrf|dependencies'` returns no unprotected mutation routes except `auth/login`.
