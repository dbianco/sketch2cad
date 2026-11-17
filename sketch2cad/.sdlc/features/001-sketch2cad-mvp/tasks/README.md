# Tasks — Sketch2CAD MVP

Feature: [prd.md](../prd.md) | [scoping-doc.md](../scoping-doc.md) | [jotdown.md](../jotdown.md)

Total: 121 tareas, 7 hitos. Estimaciones PERT en horas (esperado = (O + 4M + P) / 6).

## Resumen por hito

| Hito | Tareas | Horas esperadas | Repositorios |
|---|---|---|---|
| H0 — Fundación operativa | 27 | 144 | sketch2cad |
| H1 — Expedientes e ingesta | 18 | 120 | sketch2cad |
| H2 — Calibración y editor | 24 | 245 | sketch2cad |
| H3 — Herramientas métricas | 13 | 110 | sketch2cad |
| H4 — Exportación y auditoría | 16 | 162 | sketch2cad |
| H5 — Asistencia de IA | 12 | 116 | sketch2cad |
| H6 — Endurecimiento y validación interna | 11 | 78 | sketch2cad |

Total esperado: 976 h (≈ 24.4 semanas a 40 h; el plan del PRD asume 600 h).

## H0 — Fundación operativa

| Id | Tarea | Repositorio | Depende de | PERT (h) | P |
|---|---|---|---|---|---|
| 001-001 | [Scaffold the sketch2cad-api folder](001-scaffold-the-sketch2cad-api-folder.md) | sketch2cad | — | 5.2 | [P] |
| 001-002 | [Scaffold the sketch2cad-web folder](002-scaffold-the-sketch2cad-web-folder.md) | sketch2cad | — | 5.2 |  |
| 001-003 | [Add SQLAlchemy engine, session and declarative base](003-add-sqlalchemy-engine-session-and-declarative-base.md) | sketch2cad | 001-001 | 4.2 |  |
| 001-004 | [Create Alembic setup and the initial migration (user, session, case_file, case_member)](004-create-alembic-setup-and-the-initial-migration-user-session.md) | sketch2cad | 001-003 | 7.3 |  |
| 001-005 | [Add migration round-trip test and CI-ready script](005-add-migration-round-trip-test-and-ci-ready-script.md) | sketch2cad | 001-004 | 2.2 |  |
| 001-006 | [Add problem+json error handling and the health endpoint](006-add-problem-json-error-handling-and-the-health-endpoint.md) | sketch2cad | 001-003 | 5.2 |  |
| 001-007 | [Implement password hashing, sessions and the login/logout/me endpoints](007-implement-password-hashing-sessions-and-the-login-logout-me.md) | sketch2cad | 001-004, 001-006 | 10.3 |  |
| 001-008 | [Add account lockout after 10 failed attempts](008-add-account-lockout-after-10-failed-attempts.md) | sketch2cad | 001-007 | 4.2 |  |
| 001-009 | [Enforce CSRF tokens on every mutation](009-enforce-csrf-tokens-on-every-mutation.md) | sketch2cad | 001-007 | 4.0 |  |
| 001-010 | [Add login latency measurement test](010-add-login-latency-measurement-test.md) | sketch2cad | 001-007 | 2.0 |  |
| 001-011 | [Add the create-user CLI](011-add-the-create-user-cli.md) | sketch2cad | 001-007 | 3.2 |  |
| 001-012 | [Implement case file creation and listing](012-implement-case-file-creation-and-listing.md) | sketch2cad | 001-009, 001-004 | 8.5 |  |
| 001-013 | [Add the Procrastinate worker skeleton](013-add-the-procrastinate-worker-skeleton.md) | sketch2cad | 001-003 | 4.2 |  |
| 001-014 | [Add Dockerfile, docker-compose and the smoke script](014-add-dockerfile-docker-compose-and-the-smoke-script.md) | sketch2cad | 001-013, 001-012 | 7.3 |  |
| 001-015 | [Add the web Dockerfile with nginx](015-add-the-web-dockerfile-with-nginx.md) | sketch2cad | 001-002 | 3.2 |  |
| 001-016 | [Export openapi.json and fail CI on contract drift](016-export-openapi-json-and-fail-ci-on-contract-drift.md) | sketch2cad | 001-012 | 2.2 |  |
| 001-017 | [Generate the TypeScript API client with openapi-typescript](017-generate-the-typescript-api-client-with-openapi-typescript.md) | sketch2cad | 001-002, 001-016 | 4.2 |  |
| 001-018 | [Build the login screen and auth store](018-build-the-login-screen-and-auth-store.md) | sketch2cad | 001-017 | 6.3 |  |
| 001-019 | [Build the case list and new case form](019-build-the-case-list-and-new-case-form.md) | sketch2cad | 001-018 | 7.3 |  |
| 001-020 | [Add the license allow-list check for the API repository](020-add-the-license-allow-list-check-for-the-api-repository.md) | sketch2cad | 001-001 | 5.2 |  |
| 001-021 | [Add the license allow-list check for the web repository](021-add-the-license-allow-list-check-for-the-web-repository.md) | sketch2cad | 001-002 | 4.0 |  |
| 001-022 | [Add GitHub Actions CI for the API repository](022-add-github-actions-ci-for-the-api-repository.md) | sketch2cad | 001-005, 001-020, 001-014 | 7.3 |  |
| 001-023 | [Add GitHub Actions CI for the web repository](023-add-github-actions-ci-for-the-web-repository.md) | sketch2cad | 001-021, 001-017, 001-015 | 5.2 |  |
| 001-024 | [Add the Playwright E2E for login and case creation against the full stack](024-add-the-playwright-e2e-for-login-and-case-creation-against-t.md) | sketch2cad | 001-022, 001-019 | 9.5 |  |
| 001-025 | [Run spike 1: SVG editor performance with 2000 entities](025-run-spike-1-svg-editor-performance-with-2000-entities.md) | sketch2cad | 001-002, 001-017 | 8.3 |  |
| 001-026 | [Run spike 2: DXF byte-level reproducibility with ezdxf](026-run-spike-2-dxf-byte-level-reproducibility-with-ezdxf.md) | sketch2cad | 001-001 | 7.3 |  |
| 001-027 | [Record the D-3 outcome in the spec and Jot Down](027-record-the-d-3-outcome-in-the-spec-and-jot-down.md) | sketch2cad | 001-026, 001-025 | 1.2 |  |

## H1 — Expedientes e ingesta

| Id | Tarea | Repositorio | Depende de | PERT (h) | P |
|---|---|---|---|---|---|
| 001-028 | [Add the sources, events and job_meta migration with the immutable event trigger](028-add-the-sources-events-and-job-meta-migration-with-the-immut.md) | sketch2cad | 001-027 | 7.3 | [P] |
| 001-029 | [Implement the Storage interface and the filesystem CAS driver](029-implement-the-storage-interface-and-the-filesystem-cas-drive.md) | sketch2cad | 001-027 | 7.3 | [P] |
| 001-030 | [Implement the hash-chained event writer](030-implement-the-hash-chained-event-writer.md) | sketch2cad | 001-028 | 10.3 |  |
| 001-031 | [Implement chain verification](031-implement-chain-verification.md) | sketch2cad | 001-030 | 5.2 |  |
| 001-032 | [Add case permissions, member endpoint and access.denied events](032-add-case-permissions-member-endpoint-and-access-denied-event.md) | sketch2cad | 001-030, 001-012 | 8.5 |  |
| 001-033 | [Record auth.login and auth.failed events](033-record-auth-login-and-auth-failed-events.md) | sketch2cad | 001-030, 001-008 | 3.2 |  |
| 001-034 | [Implement source ingestion](034-implement-source-ingestion.md) | sketch2cad | 001-029, 001-032, 001-028 | 12.5 |  |
| 001-035 | [Implement original download and preview endpoints](035-implement-original-download-and-preview-endpoints.md) | sketch2cad | 001-034 | 6.3 |  |
| 001-036 | [Rasterize single-page PDFs at 300 dpi in the worker](036-rasterize-single-page-pdfs-at-300-dpi-in-the-worker.md) | sketch2cad | 001-034, 001-013 | 5.3 |  |
| 001-037 | [Implement the image quality checks](037-implement-the-image-quality-checks.md) | sketch2cad | 001-001 | 8.5 | [P] |
| 001-038 | [Run QC as a worker job and expose job status](038-run-qc-as-a-worker-job-and-expose-job-status.md) | sketch2cad | 001-037, 001-036 | 8.5 |  |
| 001-039 | [Add logging that excludes payloads and test it](039-add-logging-that-excludes-payloads-and-test-it.md) | sketch2cad | 001-034 | 4.0 |  |
| 001-040 | [Expose the event log endpoint](040-expose-the-event-log-endpoint.md) | sketch2cad | 001-032, 001-031 | 4.3 |  |
| 001-041 | [Regenerate the web client for the H1 endpoints](041-regenerate-the-web-client-for-the-h1-endpoints.md) | sketch2cad | 001-035, 001-038, 001-040, 001-024 | 1.2 |  |
| 001-042 | [Build the upload screen with quality indicator](042-build-the-upload-screen-with-quality-indicator.md) | sketch2cad | 001-041 | 10.3 |  |
| 001-043 | [Build the source viewer with pan and zoom](043-build-the-source-viewer-with-pan-and-zoom.md) | sketch2cad | 001-042 | 6.3 |  |
| 001-044 | [Add the E2E for ingestion](044-add-the-e2e-for-ingestion.md) | sketch2cad | 001-043, 001-038, 001-035 | 5.3 |  |
| 001-045 | [Document and script backup and restore](045-document-and-script-backup-and-restore.md) | sketch2cad | 001-034 | 5.2 |  |

## H2 — Calibración y editor

| Id | Tarea | Repositorio | Depende de | PERT (h) | P |
|---|---|---|---|---|---|
| 001-046 | [Add the plans, calibration, revisions, entities, catalog and artifacts migration](046-add-the-plans-calibration-revisions-entities-catalog-and-art.md) | sketch2cad | 001-040, 001-045, 001-044 | 10.3 | [P] |
| 001-047 | [Seed the default catalog (10 layers, 8 symbols)](047-seed-the-default-catalog-10-layers-8-symbols.md) | sketch2cad | 001-046 | 8.5 |  |
| 001-048 | [Implement image-to-paper and paper-to-world transforms](048-implement-image-to-paper-and-paper-to-world-transforms.md) | sketch2cad | 001-001 | 10.3 | [P] |
| 001-049 | [Implement plan creation and retrieval](049-implement-plan-creation-and-retrieval.md) | sketch2cad | 001-046, 001-032 | 8.5 |  |
| 001-050 | [Implement calibration proposal and acceptance](050-implement-calibration-proposal-and-acceptance.md) | sketch2cad | 001-049, 001-048 | 13.7 |  |
| 001-051 | [Add the rectify worker job](051-add-the-rectify-worker-job.md) | sketch2cad | 001-050 | 7.3 |  |
| 001-052 | [Implement entity create, read, patch and delete with provenance](052-implement-entity-create-read-patch-and-delete-with-provenanc.md) | sketch2cad | 001-049 | 14.7 |  |
| 001-053 | [Implement the local coordinate frame and rigid transform](053-implement-the-local-coordinate-frame-and-rigid-transform.md) | sketch2cad | 001-052 | 10.3 |  |
| 001-054 | [Implement the edit lease](054-implement-the-edit-lease.md) | sketch2cad | 001-052, 001-053 | 7.3 |  |
| 001-055 | [Implement revision freezing and reopening with canonical snapshots](055-implement-revision-freezing-and-reopening-with-canonical-sna.md) | sketch2cad | 001-052, 001-030 | 12.5 |  |
| 001-056 | [Implement plan transitions and permissions](056-implement-plan-transitions-and-permissions.md) | sketch2cad | 001-054, 001-055 | 10.3 |  |
| 001-057 | [Compute the initial metric status and provenance defaults](057-compute-the-initial-metric-status-and-provenance-defaults.md) | sketch2cad | 001-052, 001-050 | 5.3 |  |
| 001-058 | [Regenerate the web client for the H2 endpoints](058-regenerate-the-web-client-for-the-h2-endpoints.md) | sketch2cad | 001-056, 001-053, 001-051, 001-047, 001-057 | 1.2 |  |
| 001-059 | [Create the editor store with undo and redo via Immer patches](059-create-the-editor-store-with-undo-and-redo-via-immer-patches.md) | sketch2cad | 001-058 | 10.3 |  |
| 001-060 | [Build the SVG canvas with layers, pan and zoom](060-build-the-svg-canvas-with-layers-pan-and-zoom.md) | sketch2cad | 001-059 | 12.5 |  |
| 001-061 | [Add drawing tools: line, polyline, point, text, delete, reclassify](061-add-drawing-tools-line-polyline-point-text-delete-reclassify.md) | sketch2cad | 001-060 | 16.7 |  |
| 001-062 | [Add vertex editing and snapping with event logging](062-add-vertex-editing-and-snapping-with-event-logging.md) | sketch2cad | 001-061 | 12.5 |  |
| 001-063 | [Add the symbol picker and symbol placement](063-add-the-symbol-picker-and-symbol-placement.md) | sketch2cad | 001-061, 001-047 | 10.3 |  |
| 001-064 | [Build the calibration wizard (DPI, segment, 4 points)](064-build-the-calibration-wizard-dpi-segment-4-points.md) | sketch2cad | 001-058, 001-043 | 17.0 |  |
| 001-065 | [Add the local frame tool and north arrow](065-add-the-local-frame-tool-and-north-arrow.md) | sketch2cad | 001-060, 001-058 | 7.3 |  |
| 001-066 | [Add the lease banner and the review transition UI](066-add-the-lease-banner-and-the-review-transition-ui.md) | sketch2cad | 001-060, 001-058 | 13.7 |  |
| 001-067 | [Make the editor keyboard-accessible and audit it with axe](067-make-the-editor-keyboard-accessible-and-audit-it-with-axe.md) | sketch2cad | 001-062, 001-063, 001-066, 001-065 | 8.5 |  |
| 001-068 | [Measure the real editor with 2000 entities](068-measure-the-real-editor-with-2000-entities.md) | sketch2cad | 001-062 | 5.3 |  |
| 001-069 | [Add the editor E2E with every tool](069-add-the-editor-e2e-with-every-tool.md) | sketch2cad | 001-067, 001-064, 001-068 | 10.7 |  |

## H3 — Herramientas métricas

| Id | Tarea | Repositorio | Depende de | PERT (h) | P |
|---|---|---|---|---|---|
| 001-070 | [Add the evidence table and constraint columns](070-add-the-evidence-table-and-constraint-columns.md) | sketch2cad | 001-069, 001-056, 001-057 | 5.2 | [P] |
| 001-071 | [Implement the pure metric geometry functions](071-implement-the-pure-metric-geometry-functions.md) | sketch2cad | 001-048 | 17.0 | [P] |
| 001-072 | [Implement the fix-length operation](072-implement-the-fix-length-operation.md) | sketch2cad | 001-071, 001-052 | 7.3 |  |
| 001-073 | [Implement triangulation and baseline-offset operations with constraints](073-implement-triangulation-and-baseline-offset-operations-with.md) | sketch2cad | 001-071, 001-072 | 13.7 |  |
| 001-074 | [Recompute dependent entities when a reference moves](074-recompute-dependent-entities-when-a-reference-moves.md) | sketch2cad | 001-073 | 8.5 |  |
| 001-075 | [Implement declared dimensions with discrepancy flag](075-implement-declared-dimensions-with-discrepancy-flag.md) | sketch2cad | 001-052 | 5.3 | [P] |
| 001-076 | [Extend the metric status computation and endpoint](076-extend-the-metric-status-computation-and-endpoint.md) | sketch2cad | 001-073, 001-075, 001-057 | 7.3 |  |
| 001-077 | [Implement the evidence registry endpoints](077-implement-the-evidence-registry-endpoints.md) | sketch2cad | 001-070, 001-032 | 7.3 |  |
| 001-078 | [Regenerate the web client for the H3 endpoints](078-regenerate-the-web-client-for-the-h3-endpoints.md) | sketch2cad | 001-076, 001-077, 001-074 | 1.2 |  |
| 001-079 | [Add the fix-length tool](079-add-the-fix-length-tool.md) | sketch2cad | 001-078 | 6.3 |  |
| 001-080 | [Add the triangulation and baseline-offset dialogs](080-add-the-triangulation-and-baseline-offset-dialogs.md) | sketch2cad | 001-078 | 13.7 |  |
| 001-081 | [Add the evidence table and the metric status panel](081-add-the-evidence-table-and-the-metric-status-panel.md) | sketch2cad | 001-078 | 10.3 |  |
| 001-082 | [Add the E2E for metric reconstruction](082-add-the-e2e-for-metric-reconstruction.md) | sketch2cad | 001-080, 001-081, 001-079 | 7.3 |  |

## H4 — Exportación y auditoría

| Id | Tarea | Repositorio | Depende de | PERT (h) | P |
|---|---|---|---|---|---|
| 001-083 | [Export the model to DXF: header, layers, lines, polylines, points](083-export-the-model-to-dxf-header-layers-lines-polylines-points.md) | sketch2cad | 001-082, 001-027 | 12.5 |  |
| 001-084 | [Export text, symbols with attributes and evidence markers to DXF](084-export-text-symbols-with-attributes-and-evidence-markers-to.md) | sketch2cad | 001-083 | 12.5 |  |
| 001-085 | [Export dimensions to DXF with declared value override](085-export-dimensions-to-dxf-with-declared-value-override.md) | sketch2cad | 001-084 | 7.3 |  |
| 001-086 | [Apply the reproducibility settings chosen in D-3](086-apply-the-reproducibility-settings-chosen-in-d-3.md) | sketch2cad | 001-085 | 5.3 |  |
| 001-087 | [Build the PDF sheet: frame, title block, scale selection](087-build-the-pdf-sheet-frame-title-block-scale-selection.md) | sketch2cad | 001-082 | 14.7 |  |
| 001-088 | [Add scale bar, north arrow, legend, evidence table and original annex](088-add-scale-bar-north-arrow-legend-evidence-table-and-original.md) | sketch2cad | 001-087 | 14.7 |  |
| 001-089 | [Add draft watermark and the legal disclaimer](089-add-draft-watermark-and-the-legal-disclaimer.md) | sketch2cad | 001-088, 001-086 | 6.3 |  |
| 001-090 | [Build the manifest and the Ed25519 signature](090-build-the-manifest-and-the-ed25519-signature.md) | sketch2cad | 001-055, 001-031 | 14.7 | [P] |
| 001-091 | [Assemble the final package and the audit report PDF](091-assemble-the-final-package-and-the-audit-report-pdf.md) | sketch2cad | 001-090, 001-088, 001-089 | 14.7 |  |
| 001-092 | [Write the standalone sketch2cad-verify tool](092-write-the-standalone-sketch2cad-verify-tool.md) | sketch2cad | 001-091 | 10.3 |  |
| 001-093 | [Add export jobs, the artifacts endpoints and the final-export guards](093-add-export-jobs-the-artifacts-endpoints-and-the-final-export.md) | sketch2cad | 001-091, 001-056 | 14.7 |  |
| 001-094 | [Add the control-distance test across model, DXF and PDF](094-add-the-control-distance-test-across-model-dxf-and-pdf.md) | sketch2cad | 001-093 | 6.3 |  |
| 001-095 | [Document and automate the DXF viewer checks](095-document-and-automate-the-dxf-viewer-checks.md) | sketch2cad | 001-093 | 5.3 |  |
| 001-096 | [Regenerate the web client for the H4 endpoints](096-regenerate-the-web-client-for-the-h4-endpoints.md) | sketch2cad | 001-093 | 1.2 |  |
| 001-097 | [Build the export and download screen](097-build-the-export-and-download-screen.md) | sketch2cad | 001-096 | 12.5 |  |
| 001-098 | [Add the E2E: validate, export and verify the package](098-add-the-e2e-validate-export-and-verify-the-package.md) | sketch2cad | 001-097, 001-092 | 9.5 |  |

## H5 — Asistencia de IA

| Id | Tarea | Repositorio | Depende de | PERT (h) | P |
|---|---|---|---|---|---|
| 001-099 | [Implement layered configuration with per-incident-type overrides](099-implement-layered-configuration-with-per-incident-type-overr.md) | sketch2cad | 001-098, 001-095 | 8.5 |  |
| 001-100 | [Implement the suggest_lines vision pipeline](100-implement-the-suggest-lines-vision-pipeline.md) | sketch2cad | 001-099 | 21.3 |  |
| 001-101 | [Build the synthetic sketch generator and the recovery benchmark](101-build-the-synthetic-sketch-generator-and-the-recovery-benchm.md) | sketch2cad | 001-100 | 14.7 |  |
| 001-102 | [Run suggest_lines as an idempotent job with coordinate conversion](102-run-suggest-lines-as-an-idempotent-job-with-coordinate-conve.md) | sketch2cad | 001-100, 001-051 | 10.7 |  |
| 001-103 | [Implement bulk review and metric-origin promotion](103-implement-bulk-review-and-metric-origin-promotion.md) | sketch2cad | 001-102, 001-076 | 10.7 |  |
| 001-104 | [Implement OCR output normalization](104-implement-ocr-output-normalization.md) | sketch2cad | 001-099 | 3.2 |  |
| 001-105 | [Add the TrOCR crop job](105-add-the-trocr-crop-job.md) | sketch2cad | 001-104, 001-102 | 12.5 |  |
| 001-106 | [Add the ai_assist.enabled switch](106-add-the-ai-assist-enabled-switch.md) | sketch2cad | 001-105, 001-103 | 3.2 |  |
| 001-107 | [Regenerate the web client for the H5 endpoints](107-regenerate-the-web-client-for-the-h5-endpoints.md) | sketch2cad | 001-106 | 1.2 |  |
| 001-108 | [Add the suggestion layer, batch panel and confidence highlight](108-add-the-suggestion-layer-batch-panel-and-confidence-highligh.md) | sketch2cad | 001-107, 001-069 | 12.5 |  |
| 001-109 | [Add the OCR crop tool and failure display](109-add-the-ocr-crop-tool-and-failure-display.md) | sketch2cad | 001-107, 001-060 | 10.3 |  |
| 001-110 | [Add the E2E for suggestions and OCR](110-add-the-e2e-for-suggestions-and-ocr.md) | sketch2cad | 001-108, 001-109 | 7.3 |  |

## H6 — Endurecimiento y validación interna

| Id | Tarea | Repositorio | Depende de | PERT (h) | P |
|---|---|---|---|---|---|
| 001-111 | [Cover every explicit failure path (CA-11)](111-cover-every-explicit-failure-path-ca-11.md) | sketch2cad | 001-110, 001-098 | 8.5 | [P] |
| 001-112 | [Add the two-user authorization matrix test (CA-12)](112-add-the-two-user-authorization-matrix-test-ca-12.md) | sketch2cad | 001-110, 001-098 | 7.3 | [P] |
| 001-113 | [Label CI tests by acceptance criterion and gate on them](113-label-ci-tests-by-acceptance-criterion-and-gate-on-them.md) | sketch2cad | 001-111, 001-112 | 5.3 |  |
| 001-114 | [Final license and dependency audit (CA-13)](114-final-license-and-dependency-audit-ca-13.md) | sketch2cad | 001-110, 001-098 | 3.2 | [P] |
| 001-115 | [Prepare the validation set: 10 hand-drawn sketches, the printed pattern and the synthetic set](115-prepare-the-validation-set-10-hand-drawn-sketches-the-printe.md) | sketch2cad | 001-098 | 14.7 | [P] |
| 001-116 | [Measure G-1: time against FreeCAD on at least 10 sketches](116-measure-g-1-time-against-freecad-on-at-least-10-sketches.md) | sketch2cad | 001-115, 001-113 | 10.3 |  |
| 001-117 | [Measure G-2: declared dimensions versus exported values](117-measure-g-2-declared-dimensions-versus-exported-values.md) | sketch2cad | 001-116 | 7.3 |  |
| 001-118 | [Measure G-3: suggestion acceptance and time with and without suggestions](118-measure-g-3-suggestion-acceptance-and-time-with-and-without.md) | sketch2cad | 001-116 | 7.3 |  |
| 001-119 | [Run the backup and restore check](119-run-the-backup-and-restore-check.md) | sketch2cad | 001-113 | 3.2 |  |
| 001-120 | [Scan test logs for coordinates and sketch file names](120-scan-test-logs-for-coordinates-and-sketch-file-names.md) | sketch2cad | 001-113 | 3.2 |  |
| 001-121 | [Write the validation report and close the MVP](121-write-the-validation-report-and-close-the-mvp.md) | sketch2cad | 001-117, 001-118, 001-119, 001-120, 001-114 | 7.3 |  |

## Ruta crítica

Secuencia de dependencias más larga por horas esperadas: 001-001 → 001-003 → 001-004 → 001-007 → 001-009 → 001-012 → 001-016 → 001-017 → 001-025 → 001-027 → 001-028 → 001-030 → 001-032 → 001-034 → 001-036 → 001-038 → 001-041 → 001-042 → 001-043 → 001-044 → 001-046 → 001-049 → 001-052 → 001-053 → 001-054 → 001-056 → 001-058 → 001-059 → 001-060 → 001-061 → 001-062 → 001-067 → 001-069 → 001-070 → 001-077 → 001-078 → 001-080 → 001-082 → 001-083 → 001-084 → 001-085 → 001-086 → 001-089 → 001-091 → 001-093 → 001-096 → 001-097 → 001-098 → 001-099 → 001-100 → 001-102 → 001-105 → 001-106 → 001-107 → 001-108 → 001-110 → 001-111 → 001-113 → 001-116 → 001-117 → 001-121 (512 h).

| Id | Tarea | Horas |
|---|---|---|
| 001-001 | Scaffold the sketch2cad-api folder | 5.2 |
| 001-003 | Add SQLAlchemy engine, session and declarative base | 4.2 |
| 001-004 | Create Alembic setup and the initial migration (user, session, case_file, case_member) | 7.3 |
| 001-007 | Implement password hashing, sessions and the login/logout/me endpoints | 10.3 |
| 001-009 | Enforce CSRF tokens on every mutation | 4.0 |
| 001-012 | Implement case file creation and listing | 8.5 |
| 001-016 | Export openapi.json and fail CI on contract drift | 2.2 |
| 001-017 | Generate the TypeScript API client with openapi-typescript | 4.2 |
| 001-025 | Run spike 1: SVG editor performance with 2000 entities | 8.3 |
| 001-027 | Record the D-3 outcome in the spec and Jot Down | 1.2 |
| 001-028 | Add the sources, events and job_meta migration with the immutable event trigger | 7.3 |
| 001-030 | Implement the hash-chained event writer | 10.3 |
| 001-032 | Add case permissions, member endpoint and access.denied events | 8.5 |
| 001-034 | Implement source ingestion | 12.5 |
| 001-036 | Rasterize single-page PDFs at 300 dpi in the worker | 5.3 |
| 001-038 | Run QC as a worker job and expose job status | 8.5 |
| 001-041 | Regenerate the web client for the H1 endpoints | 1.2 |
| 001-042 | Build the upload screen with quality indicator | 10.3 |
| 001-043 | Build the source viewer with pan and zoom | 6.3 |
| 001-044 | Add the E2E for ingestion | 5.3 |
| 001-046 | Add the plans, calibration, revisions, entities, catalog and artifacts migration | 10.3 |
| 001-049 | Implement plan creation and retrieval | 8.5 |
| 001-052 | Implement entity create, read, patch and delete with provenance | 14.7 |
| 001-053 | Implement the local coordinate frame and rigid transform | 10.3 |
| 001-054 | Implement the edit lease | 7.3 |
| 001-056 | Implement plan transitions and permissions | 10.3 |
| 001-058 | Regenerate the web client for the H2 endpoints | 1.2 |
| 001-059 | Create the editor store with undo and redo via Immer patches | 10.3 |
| 001-060 | Build the SVG canvas with layers, pan and zoom | 12.5 |
| 001-061 | Add drawing tools: line, polyline, point, text, delete, reclassify | 16.7 |
| 001-062 | Add vertex editing and snapping with event logging | 12.5 |
| 001-067 | Make the editor keyboard-accessible and audit it with axe | 8.5 |
| 001-069 | Add the editor E2E with every tool | 10.7 |
| 001-070 | Add the evidence table and constraint columns | 5.2 |
| 001-077 | Implement the evidence registry endpoints | 7.3 |
| 001-078 | Regenerate the web client for the H3 endpoints | 1.2 |
| 001-080 | Add the triangulation and baseline-offset dialogs | 13.7 |
| 001-082 | Add the E2E for metric reconstruction | 7.3 |
| 001-083 | Export the model to DXF: header, layers, lines, polylines, points | 12.5 |
| 001-084 | Export text, symbols with attributes and evidence markers to DXF | 12.5 |
| 001-085 | Export dimensions to DXF with declared value override | 7.3 |
| 001-086 | Apply the reproducibility settings chosen in D-3 | 5.3 |
| 001-089 | Add draft watermark and the legal disclaimer | 6.3 |
| 001-091 | Assemble the final package and the audit report PDF | 14.7 |
| 001-093 | Add export jobs, the artifacts endpoints and the final-export guards | 14.7 |
| 001-096 | Regenerate the web client for the H4 endpoints | 1.2 |
| 001-097 | Build the export and download screen | 12.5 |
| 001-098 | Add the E2E: validate, export and verify the package | 9.5 |
| 001-099 | Implement layered configuration with per-incident-type overrides | 8.5 |
| 001-100 | Implement the suggest_lines vision pipeline | 21.3 |
| 001-102 | Run suggest_lines as an idempotent job with coordinate conversion | 10.7 |
| 001-105 | Add the TrOCR crop job | 12.5 |
| 001-106 | Add the ai_assist.enabled switch | 3.2 |
| 001-107 | Regenerate the web client for the H5 endpoints | 1.2 |
| 001-108 | Add the suggestion layer, batch panel and confidence highlight | 12.5 |
| 001-110 | Add the E2E for suggestions and OCR | 7.3 |
| 001-111 | Cover every explicit failure path (CA-11) | 8.5 |
| 001-113 | Label CI tests by acceptance criterion and gate on them | 5.3 |
| 001-116 | Measure G-1: time against FreeCAD on at least 10 sketches | 10.3 |
| 001-117 | Measure G-2: declared dimensions versus exported values | 7.3 |
| 001-121 | Write the validation report and close the MVP | 7.3 |

Con una sola persona, la ruta crítica no acorta el calendario: el tiempo total es la suma de todas las tareas. Sirve para saber qué recortar (H5) y qué no.

## Cobertura de requisitos

| Requisito | Cubierto por | Notas |
|---|---|---|
| REQ-01 | 001-004, 001-007, 001-008, 001-009, 001-010, 001-011, 001-012, 001-018, 001-019, 001-024, 001-032, 001-033, 001-035, 001-040, 001-044, 001-112 | |
| REQ-02 | 001-028, 001-029, 001-034, 001-035, 001-036, 001-037, 001-038, 001-042, 001-043, 001-044 | |
| REQ-03 | 001-046, 001-048, 001-049, 001-050, 001-051, 001-064 | |
| REQ-04 | 001-048, 001-053, 001-065 | |
| REQ-05 | 001-025, 001-046, 001-052, 001-054, 001-059, 001-060, 001-061, 001-062, 001-063, 001-066, 001-067, 001-068, 001-069 | |
| REQ-06 | 001-070, 001-071, 001-072, 001-073, 001-074, 001-075, 001-079, 001-082 | |
| REQ-07 | 001-057, 001-076, 001-081, 001-082 | |
| REQ-08 | 001-070, 001-077, 001-081, 001-082 | |
| REQ-09 | 001-046, 001-047, 001-063 | |
| REQ-10 | 001-046, 001-052, 001-055, 001-056, 001-066, 001-069, 001-103, 001-119 | |
| REQ-11 | 001-028, 001-030, 001-031, 001-033, 001-039, 001-040, 001-092, 001-112, 001-119, 001-120 | |
| REQ-12 | 001-026, 001-027, 001-083, 001-084, 001-085, 001-086, 001-093, 001-094, 001-095 | |
| REQ-13 | 001-087, 001-088, 001-089, 001-093, 001-094 | |
| REQ-14 | 001-089, 001-090, 001-091, 001-092, 001-093, 001-097, 001-098 | |
| REQ-15 | 001-006, 001-038, 001-102, 001-109, 001-111, 001-113 | |
| REQ-16 | 001-004, 001-005, 001-006, 001-010, 001-013, 001-014, 001-015, 001-016, 001-017, 001-020, 001-021, 001-022, 001-023, 001-024, 001-025, 001-026, 001-027, 001-113, 001-114 | |
| REQ-17 | 001-100, 001-101, 001-102, 001-103, 001-106, 001-108, 001-110, 001-118 | |
| REQ-18 | 001-104, 001-105, 001-106, 001-109, 001-110 | |
| REQ-19 | 001-099, 001-106 | |
| REQ-20 | 001-115, 001-116, 001-117, 001-118, 001-121 | |
| REQ-21 | — | P2, fuera de este MVP (PRD, Non-Goals y sección 8) |
| REQ-22 | — | P2, fuera de este MVP (PRD, Non-Goals y sección 8) |
| REQ-23 | — | P2, fuera de este MVP (PRD, Non-Goals y sección 8) |
| REQ-24 | — | P2, fuera de este MVP (PRD, Non-Goals y sección 8) |
| US-01 | 001-012, 001-019, 001-024 | |
| US-02 | 001-034, 001-035, 001-042, 001-043, 001-044 | |
| US-03 | 001-050, 001-064, 001-069 | |
| US-04 | 001-049, 001-064 | |
| US-05 | 001-053, 001-065 | |
| US-06 | 001-052, 001-059, 001-061, 001-062, 001-069 | |
| US-07 | 001-071, 001-072, 001-079 | |
| US-08 | 001-071, 001-073, 001-080, 001-082 | |
| US-09 | 001-071, 001-073, 001-080 | |
| US-10 | 001-077, 001-081 | |
| US-11 | 001-057, 001-076, 001-081 | |
| US-12 | 001-103, 001-108, 001-110 | |
| US-13 | 001-105, 001-109, 001-110 | |
| US-14 | 001-055, 001-056, 001-066 | |
| US-15 | 001-089, 001-093, 001-097 | |
| US-16 | 001-091, 001-093, 001-097, 001-098 | |
| US-17 | 001-092, 001-098 | |
| US-18 | 001-109, 001-111 | |
| US-19 | 001-007, 001-018, 001-032, 001-112 | |
| V-01 | 001-034, 001-042 | |
| V-02 | 001-034, 001-042 | |
| V-03 | 001-034, 001-042 | |
| V-04 | 001-071, 001-079, 001-080 | |
| V-05 | 001-050, 001-064 | |
| V-06 | 001-073, 001-080 | |
| V-07 | 001-073, 001-080 | |
| V-08 | 001-050 | |
| V-09 | 001-056, 001-066 | |
| V-10 | 001-056, 001-066 | |
| V-11 | 001-093, 001-097 | |
| V-12 | 001-054, 001-066 | |

- Requisitos sin tarea: 4 (REQ-21, REQ-22, REQ-23, REQ-24). Los cuatro REQ P2 están excluidos del alcance por decisión del PRD.
- Tareas sin requisito: 0 (las `Implements: INFRA` no cuentan).

## Colisiones de archivos resueltas

2 pares de tareas del mismo hito compartían archivos y quedaron secuenciados (la segunda depende de la primera):

- 001-017 → 001-025: sketch2cad-web/package.json
- 001-053 → 001-054: sketch2cad-api/src/sketch2cad/api/routers/plans.py
