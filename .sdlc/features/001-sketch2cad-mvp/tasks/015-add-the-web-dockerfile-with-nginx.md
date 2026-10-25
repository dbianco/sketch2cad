---
id: 001-015
title: "Add the web Dockerfile with nginx"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-002]
implements: [REQ-16]
files: ["sketch2cad-web/Dockerfile", "sketch2cad-web/nginx.conf", "sketch2cad-web/.dockerignore"]
parallelizable: false
estimate: { optimistic: 2, likely: 3, pessimistic: 5 }
labels: [ai-sdlc, typescript]
---

## Description

Multi-stage build: `pnpm build` then nginx serving `dist/` with SPA fallback and a reverse proxy of `/api` to the `api` service. Healthcheck on `/`. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-16

## Acceptance Criteria

- [ ] `docker build -t sketch2cad-web .` exits 0.
- [ ] `docker run` of the image answers `GET /` with 200 and `GET /unknown-route` with 200 serving `index.html` (SPA fallback), verified with `curl -s -o /dev/null -w '%{http_code}'`.
