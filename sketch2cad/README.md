# Sketch2CAD

A web application that turns the field sketch of a traffic accident, a crime scene or a visual inspection into an editable, measurable and auditable 2D plan, and exports it to DXF and technical PDF.

> **Status:** MVP under development. The code lives in `sketch2cad-api/` and `sketch2cad-web/`. The product is a technical draft and is not meant to be used as evidence until it has had a legal review.

## Core idea

A hand-drawn sketch is normally not to scale. The real metric information is in the **written dimensions** and in reference measurements (triangulation from two fixed points, baseline and offset), not in the pixels. That is why Sketch2CAD rebuilds the geometry from the dimensions the technician declares:

- The **declared dimension wins** over the derived measurement; the system never corrects one on its own.
- **AI only proposes** (suggested polylines and assisted OCR of dimensions); a person accepts, corrects or rejects.
- Every plan has a **metric status** (`metric`, `approximate`, `non_metric`) that is printed on the DXF and the PDF.
- Every action is recorded in a **hash-chained event log**, and the final package carries a signed manifest that an external auditor can verify without access to the server.

## MVP scope

Included: case files and immutable hashed sources, image-to-paper calibration, local coordinate system, 2D editor in the browser, metric tools (fix length, triangulation, baseline plus offset), review and validation with frozen revisions, export to DXF and to PDF at scale, signed final package and independent verifier. AI suggestions are optional.

Not included: 3D, georeferencing, automatic evidence detection, autonomous expert analysis, external AI services, MFA or institutional signing.

## Plan

One person full time, 15 weeks, seven sequential milestones.

| Milestone | Content | Weeks |
|---|---|---|
| H0 | Foundation: repositories, docker-compose, local auth, CI and licenses, two technical checks | 1 |
| H1 | Case files, ingestion with hashing and quality control, chained events | 2 |
| H2 | Calibration, local system, SVG editor, revisions | 3 |
| H3 | Metric tools, evidence table, metric status | 2 |
| H4 | DXF and PDF export, signed manifest, verifier | 3 |
| H5 | AI assistance (can be cut) | 2 |
| H6 | Hardening and internal validation with 10 or more own sketches | 2 |

## Stack

Python 3.12 with FastAPI, SQLAlchemy and Alembic; Procrastinate worker on PostgreSQL 16; React 18 with TypeScript and Vite; SVG editor; OpenCV, scikit-image and shapely; ezdxf for DXF and ReportLab for PDF; Ed25519 signing with canonical JSON (RFC 8785). Everything runs with `docker compose`. The code lives in this repository, in two folders: `sketch2cad-api/` and `sketch2cad-web/`.

## Structure

| Path | Content |
|---|---|
| `docs/specs/Sketch2CAD_MVP.md` | Current MVP specification (v2.0), the normative source |
| `docs/specs/archive/` | Earlier specifications, kept only as background |
| `docs/evaluation/` | Evaluation of the original specifications |
| `docs/prototype/` | Six-screen prototype (visual reference) |
| `docs/references/` | Reference material (the PDF of the article is not versioned) |
| `sketch2cad-api/`, `sketch2cad-web/` | Code: API and worker, and the web editor |
| `validation/` | Scripts and data for the internal validation (H6) |
| `.sdlc/features/001-sketch2cad-mvp/` | `prd.md`, `scoping-doc.md` and `jotdown.md` of the feature. It must be at the root because the SDLC plugin looks for it there. |

## Where to start

1. Read `docs/specs/Sketch2CAD_MVP.md` for the whole product.
2. Read `.sdlc/features/001-sketch2cad-mvp/prd.md` for goals, requirements and decisions.
3. Read `.sdlc/features/001-sketch2cad-mvp/jotdown.md` for the implementation detail of each milestone.
4. Read `docs/running-locally.md` to run the stack on your machine.

## License

MIT License. The full text is in [`LICENSE`](LICENSE).
