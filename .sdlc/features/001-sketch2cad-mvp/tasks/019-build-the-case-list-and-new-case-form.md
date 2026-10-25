---
id: 001-019
title: "Build the case list and new case form"
status: in_review
milestone: "H0 — Fundación operativa"
repository: sketch2cad
depends_on: [001-018]
implements: [US-01, REQ-01]
files: ["sketch2cad-web/src/features/cases/CaseListPage.tsx", "sketch2cad-web/src/features/cases/NewCaseForm.tsx", "sketch2cad-web/src/features/cases/CaseListPage.test.tsx", "sketch2cad-web/src/app/routes.tsx"]
parallelizable: false
estimate: { optimistic: 4, likely: 7, pessimistic: 12 }
labels: [ai-sdlc, typescript]
---

## Description

Table following prototype screen 1 (`docs/prototype/Main.dc.html` in the docs repo): code, incident type, date, declared location, status. Form with required fields and decimal-free validation messages in Spanish. Run commands from the `sketch2cad-web/` folder.

Implements: US-01, REQ-01

## Acceptance Criteria

- [ ] `pnpm test --run src/features/cases/CaseListPage.test.tsx` passes: lists cases from a mocked API and creating one adds a row with the returned code.
- [ ] Submitting the form with an empty description shows the message "Ingresa una descripción" and sends no request.
