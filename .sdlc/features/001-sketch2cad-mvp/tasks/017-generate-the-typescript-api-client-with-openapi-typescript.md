---
id: 001-017
title: "Generate the TypeScript API client with openapi-typescript"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-002, 001-016]
implements: [REQ-16]
files: ["sketch2cad-web/scripts/generate-client.sh", "sketch2cad-web/src/api/schema.d.ts", "sketch2cad-web/src/api/client.ts", "sketch2cad-web/src/api/client.test.ts", "sketch2cad-web/package.json"]
parallelizable: false
estimate: { optimistic: 2, likely: 4, pessimistic: 7 }
labels: [ai-sdlc, typescript]
---

## Description

Decision D-7. `generate-client.sh` reads `../sketch2cad-api/openapi.json` from the same commit and runs `openapi-typescript` to `src/api/schema.d.ts`; `client.ts` wraps `fetch` with credentials and the `X-CSRF-Token` header read from the `s2c_csrf` cookie. A CI step runs the script and fails when `git diff --exit-code src/api/schema.d.ts` is non-empty. Run commands from the `sketch2cad-web/` folder.

Implements: REQ-16

## Acceptance Criteria

- [ ] `bash scripts/generate-client.sh && git diff --exit-code src/api/schema.d.ts` exits 0 on a clean checkout (the schema comes from `../sketch2cad-api/openapi.json` of the same commit).
- [ ] `pnpm test --run src/api/client.test.ts` passes: a mutation request carries `X-CSRF-Token` equal to the cookie value; a GET does not.
- [ ] `rg -n 'openapi-typescript' package.json` shows it as a devDependency.
