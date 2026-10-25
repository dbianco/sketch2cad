---
id: 001-007
title: "Implement password hashing, sessions and the login/logout/me endpoints"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-004, 001-006]
implements: [REQ-01, US-19]
files: ["sketch2cad-api/src/sketch2cad/auth/__init__.py", "sketch2cad-api/src/sketch2cad/auth/passwords.py", "sketch2cad-api/src/sketch2cad/auth/sessions.py", "sketch2cad-api/src/sketch2cad/api/deps.py", "sketch2cad-api/src/sketch2cad/api/routers/auth.py", "sketch2cad-api/tests/auth/test_login.py"]
parallelizable: false
estimate: { optimistic: 6, likely: 10, pessimistic: 16 }
labels: [ai-sdlc, python]
---

## Description

Argon2id via argon2-cffi defaults. Login creates a `session` row storing only `sha256(token)`; the raw token goes into cookie `s2c_session` (HttpOnly, Secure outside `APP_ENV=dev`, SameSite=Lax); a second cookie `s2c_csrf` carries the CSRF token. Session lifetime `auth.session_ttl_minutes` default 480. Same 401 body for unknown user and wrong password. Jot Down: Milestone 1, section 4. Run commands from the `sketch2cad-api/` folder.

Implements: REQ-01, US-19

## Acceptance Criteria

- [ ] `POST /api/v1/auth/login` with valid credentials returns 200 and `Set-Cookie` headers containing `HttpOnly` and `SameSite=Lax` for `s2c_session`; `GET /api/v1/auth/me` then returns the user (`tests/auth/test_login.py`).
- [ ] Unknown user and wrong password return the identical 401 problem body (asserted by comparing the two responses).
- [ ] The database never contains the raw session token: `SELECT count(*) FROM session WHERE token_sha256 = <raw cookie value>` returns 0.
- [ ] `POST /api/v1/auth/logout` sets `revoked_at` and a following `GET /auth/me` returns 401.
