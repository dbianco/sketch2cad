---
id: 001-002
title: "Scaffold the sketch2cad-web folder"
status: todo
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: []
implements: [INFRA]
files: ["sketch2cad-web/package.json", "sketch2cad-web/pnpm-lock.yaml", "sketch2cad-web/tsconfig.json", "sketch2cad-web/vite.config.ts", "sketch2cad-web/eslint.config.js", "sketch2cad-web/index.html", "sketch2cad-web/src/main.tsx", "sketch2cad-web/src/app/App.tsx", "sketch2cad-web/src/app/App.test.tsx", "sketch2cad-web/README.md", "sketch2cad-web/.gitignore"]
parallelizable: false
estimate: { optimistic: 3, likely: 5, pessimistic: 8 }
labels: [ai-sdlc, typescript]
---

## Description

Create the `sketch2cad-web/` folder inside the existing sketch2cad repository (single-repository decision D-1). Create the React 18 + TypeScript + Vite project with `pnpm`, ESLint, vitest and Testing Library (decision D-5). Dev server proxies `/api` to `http://localhost:8000`. `App.tsx` renders a placeholder routed shell (react-router). Jot Down: Milestone 1, section 5. Run commands from the `sketch2cad-web/` folder.

Implements: INFRA

## Acceptance Criteria

- [ ] `pnpm install --frozen-lockfile` succeeds on a clean checkout.
- [ ] `pnpm lint`, `pnpm exec tsc --noEmit` and `pnpm test --run` exit 0; `App.test.tsx` asserts the shell renders a heading with role `heading`.
- [ ] `pnpm build` writes `dist/index.html`.
- [ ] `rg -n 'http://localhost:8000' src/` returns no matches (the proxy address lives only in `vite.config.ts`).
