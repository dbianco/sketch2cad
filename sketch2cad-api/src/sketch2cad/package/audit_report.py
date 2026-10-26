"""Audit report PDF of the final package (spec 14.3, REQ-14, US-16).

The report is the readable form of the package manifest (spec 14.2): case, plan and
revision, the table of artifacts with their SHA-256, the events chain summary, calibration,
local frame, software and actors; then the events of the plan (who, what, when, hash and a
short summary of the change), the validity note of spec section 21, the scope of the
signature ("La firma prueba integridad y origen técnico del paquete, no autoría pericial."),
the legal disclaimer while ``legal.disclaimer_enabled`` is true and the verification
instructions (the full ones are in ``VERIFY.md``).

The report cannot carry its own hash, nor the signature, because the manifest lists the
report's hash and the signature covers the manifest: its artifacts table names
``audit.pdf`` and points to ``manifest.json`` for it.

Reproducibility (CA-08): the canvas comes from :func:`sketch2cad.export.pdf.new_canvas`
(``invariant=1``, uncompressed content, dates from ``frozen_at``, ``/ID`` from a seed), the
pages are A4 portrait, long tables continue on new pages with their header repeated, and
every value is rendered from the given mappings with sorted keys, so the same inputs give
the same bytes.
"""

# The ReportLab stubs leave canvas callbacks and ``simpleSplit`` partly unannotated (Unknown).
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

import io
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from sketch2cad.export.disclaimer import ExportLegal, disclaimer_band_height, draw_disclaimer
from sketch2cad.export.pdf import new_canvas
from sketch2cad.export.pdf_sheet import (
    LABEL_FONT,
    METRIC_STATUS_TEXT,
    NO_VALUE,
    VALUE_FONT,
    Rect,
    SheetSpec,
    fit_text,
    mm_to_pt,
    sheet_layout,
)

PAGE = SheetSpec("A4 vertical", 210.0, 297.0, 0.0)
MARGIN_MM = 15.0
TITLE_MM = 5.0
HEADING_MM = 3.6
TEXT_MM = 2.6
SMALL_MM = 2.2
LEADING = 1.45
SECTION_GAP_MM = 4.0
CELL_PADDING_MM = 1.0
SHORT_HASH = 12
AUDIT_FILENAME = "audit.pdf"
SELF_HASH_TEXT = "este documento: su SHA-256 está en manifest.json"
SIGNATURE_SCOPE = (
    "La firma prueba integridad y origen técnico del paquete, no autoría pericial. "
    "No sustituye la firma personal del perito ni un sello de tiempo."
)
VALIDITY_NOTE = (
    "Sketch2CAD es una herramienta de documentación y asistencia técnica. La validez "
    "probatoria, la admisibilidad del resultado, el régimen de firma y la cadena de custodia "
    "deben ser revisados y aprobados por la autoridad jurídica e institucional competente "
    "antes de usar el sistema en producción. El hash y la firma del paquete prueban integridad "
    "y origen técnico; no prueban por sí solos la autoría pericial ni la custodia física."
)
VERIFICATION_STEPS = (
    "1. Ejecute sketch2cad-verify sobre el ZIP: recalcula el SHA-256 de cada artefacto, "
    "reconstruye la cadena de eventos de events.json y verifica la firma de manifest.json "
    "con la clave pública incluida; sale con código 0 si todo coincide.",
    "2. Sin la herramienta: compare el SHA-256 de cada archivo (sha256sum) con la tabla de "
    "artefactos de este reporte y de manifest.json.",
    "3. Compruebe que el identificador de la clave pública coincide con el publicado por la "
    "institución por un canal independiente: la clave incluida en el ZIP solo prueba que el "
    "manifiesto no cambió desde que se firmó con ella.",
    "4. Las instrucciones completas, también para la verificación manual de la firma, están "
    "en VERIFY.md.",
)
ARTIFACT_COLUMNS = (("Tipo", 30.0), ("Archivo", 38.0), ("SHA-256", 0.0))
EVENT_COLUMNS = (
    ("Id", 10.0),
    ("Evento", 32.0),
    ("Actor", 26.0),
    ("Fecha (UTC)", 38.0),
    ("Hash", 20.0),
    ("Resumen", 0.0),
)


def _json_text(value: Any) -> str:
    """Compact JSON with sorted keys, the readable form of nested values."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(", ", ": "))


def _actor_text(actor: Mapping[str, Any] | None, at: str | None = None) -> str:
    if not actor:
        return NO_VALUE
    name = actor.get("display_name") or actor.get("user_id") or NO_VALUE
    text = f"{name} ({actor.get('role') or NO_VALUE}; {actor.get('user_id') or NO_VALUE})"
    return f"{text}, {at}" if at else text


def event_summary(event: Mapping[str, Any]) -> str:
    """Short before/after summary: the reason, the entity and the changed keys."""
    parts: list[str] = []
    after = event.get("after")
    before = event.get("before")
    if isinstance(after, Mapping):
        for key in ("operation", "from", "to", "state"):
            if key in after:
                parts.append(f"{key}={after[key]}")
    if event.get("entity_id"):
        parts.append(f"entidad {str(event['entity_id'])[:8]}")
    keys = sorted(
        set(before.keys() if isinstance(before, Mapping) else ())
        | set(after.keys() if isinstance(after, Mapping) else ())
    )
    if keys:
        parts.append("campos: " + ", ".join(str(key) for key in keys))
    if event.get("reason"):
        parts.append(f"motivo: {event['reason']}")
    return "; ".join(parts) or NO_VALUE


class _Report:
    """A flowing A4 portrait document: cursor, page breaks, header and footer."""

    def __init__(self, canvas: Canvas, header: str) -> None:
        self.canvas = canvas
        self.header = header
        self.page = 1
        self.left = MARGIN_MM
        self.width = PAGE.width_mm - 2 * MARGIN_MM
        self.bottom = MARGIN_MM + 6.0
        self.y = 0.0
        self._start_page()

    def _start_page(self) -> None:
        top = PAGE.height_mm - MARGIN_MM
        self.canvas.setFillColorRGB(0, 0, 0)
        self.canvas.setFont(LABEL_FONT, mm_to_pt(SMALL_MM))
        self.canvas.drawString(mm_to_pt(self.left), mm_to_pt(top), self.header)
        self.y = top - 4.0

    def _end_page(self) -> None:
        self.canvas.setFont(LABEL_FONT, mm_to_pt(SMALL_MM))
        self.canvas.drawRightString(
            mm_to_pt(self.left + self.width), mm_to_pt(MARGIN_MM), f"Página {self.page}"
        )
        self.canvas.showPage()
        self.page += 1

    def ensure(self, height_mm: float) -> bool:
        """Start a new page unless ``height_mm`` fits; True when a page was started."""
        if self.y - height_mm >= self.bottom:
            return False
        self._end_page()
        self._start_page()
        return True

    def line(self, text: str, font: str, size_mm: float, x_mm: float = 0.0) -> None:
        step = size_mm * LEADING
        self.ensure(step)
        self.y -= step
        self.canvas.setFont(font, mm_to_pt(size_mm))
        self.canvas.drawString(mm_to_pt(self.left + x_mm), mm_to_pt(self.y), text)

    def paragraph(self, text: str, font: str = LABEL_FONT, size_mm: float = TEXT_MM) -> None:
        for line in simpleSplit(text, font, mm_to_pt(size_mm), mm_to_pt(self.width)):
            self.line(str(line), font, size_mm)

    def heading(self, text: str) -> None:
        self.y -= SECTION_GAP_MM
        self.ensure(HEADING_MM * LEADING + 3 * TEXT_MM * LEADING)
        self.line(text, VALUE_FONT, HEADING_MM)
        self.y -= 1.0

    def key_values(self, rows: Sequence[tuple[str, str]], label_mm: float = 42.0) -> None:
        size = mm_to_pt(TEXT_MM)
        room = mm_to_pt(self.width - label_mm)
        for label, value in rows:
            lines = [str(line) for line in simpleSplit(value, LABEL_FONT, size, room)] or [""]
            for index, line in enumerate(lines):
                self.line(label if index == 0 else "", VALUE_FONT, TEXT_MM)
                self.canvas.setFont(LABEL_FONT, size)
                self.canvas.drawString(mm_to_pt(self.left + label_mm), mm_to_pt(self.y), line)

    def table(self, columns: Sequence[tuple[str, float]], rows: Sequence[Sequence[str]]) -> None:
        """Fixed-width columns (width 0 takes the rest); cells are cut with an ellipsis."""
        fixed = sum(width for _, width in columns)
        widths = [width or self.width - fixed for _, width in columns]
        self._table_row([title for title, _ in columns], widths, VALUE_FONT)
        for row in rows:
            if self.ensure(SMALL_MM * LEADING):
                self._table_row([title for title, _ in columns], widths, VALUE_FONT)
            self._table_row(row, widths, LABEL_FONT)

    def _table_row(self, cells: Sequence[str], widths: Sequence[float], font: str) -> None:
        step = SMALL_MM * LEADING
        self.ensure(step)
        self.y -= step
        size = mm_to_pt(SMALL_MM)
        self.canvas.setFont(font, size)
        x = self.left
        for cell, width in zip(cells, widths, strict=True):
            text = fit_text(cell, font, size, mm_to_pt(width - CELL_PADDING_MM))
            self.canvas.drawString(mm_to_pt(x), mm_to_pt(self.y), text)
            x += width
        if font == VALUE_FONT:
            self.canvas.setLineWidth(mm_to_pt(0.18))
            rule = self.y - 0.8
            self.canvas.line(
                mm_to_pt(self.left),
                mm_to_pt(rule),
                mm_to_pt(self.left + self.width),
                mm_to_pt(rule),
            )

    def disclaimer(self, text: str) -> None:
        height = disclaimer_band_height(text, self.width)
        self.y -= SECTION_GAP_MM
        self.ensure(height)
        draw_disclaimer(self.canvas, Rect(self.left, self.y - height, self.width, height), text)
        self.y -= height

    def finish(self) -> None:
        self._end_page()
        self.canvas.save()


def _package_rows(
    manifest: Mapping[str, Any],
    case_title: str,
    revision_id: str,
    source_name: str,
    public_key_id: str,
) -> list[tuple[str, str]]:
    case = manifest["case_file"]
    revision = manifest["revision"]
    chain = manifest["events_chain"]
    software = manifest["software"]
    status = str(manifest["metric_status"])
    return [
        ("Expediente", f"{case['code']} - {case_title}"),
        ("Id del expediente", str(case["id"])),
        ("Plano", str(manifest["plan_id"])),
        ("Revisión", f"{revision['number']} (id {revision_id})"),
        ("Snapshot SHA-256", str(revision["snapshot_sha256"])),
        ("Estado métrico", METRIC_STATUS_TEXT.get(status, status)),
        ("Catálogo", str(manifest["catalog_version"])),
        ("Original cargado", source_name),
        ("Cadena de eventos", f"{chain['count']} eventos; último hash {chain['last_hash']}"),
        ("Software", _json_text(software)),
        ("Validado por", _actor_text(manifest["validated_by"], manifest["validated_at"])),
        ("Exportado por", _actor_text(manifest["exported_by"], manifest["exported_at"])),
        ("Zona horaria", str(manifest["timezone_display"])),
        ("Firma", f"ed25519-jcs-v1, clave {public_key_id}"),
        ("Versión del manifiesto", str(manifest["manifest_version"])),
    ]


def _mapping_rows(value: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    if value is None:
        return [("", NO_VALUE)]
    return [(str(key), _json_text(value[key])) for key in sorted(value)]


def build_audit_report(
    manifest: Mapping[str, Any],
    *,
    case_title: str,
    revision_id: str,
    source_name: str,
    plan_events: Sequence[Mapping[str, Any]],
    public_key_id: str,
    frozen_at: datetime,
    generator_version: str,
    legal: ExportLegal | None = None,
) -> bytes:
    """PDF bytes of the audit report of ``manifest`` (unsigned, without ``audit.pdf``).

    ``plan_events`` are the plan's events oldest first, as served by the events endpoint;
    ``source_name`` describes the uploaded original (name and media type). The legal
    disclaimer is printed unless ``legal`` disables it (default enabled).
    """
    revision = manifest["revision"]
    code = manifest["case_file"]["code"]
    buffer = io.BytesIO()
    canvas = new_canvas(
        buffer,
        sheet_layout(PAGE, title_rows=0),
        title=f"Reporte de auditoría {code} - revisión {revision['number']}",
        subject=(
            f"plan_id={manifest['plan_id']}; revision_number={revision['number']}; "
            f"snapshot_sha256={revision['snapshot_sha256']}"
        ),
        creator=f"Sketch2CAD {generator_version}",
        frozen_at=frozen_at,
        id_seed=f"audit:{manifest['plan_id']}:{revision['snapshot_sha256']}",
    )
    report = _Report(
        canvas, f"Expediente {code} · Plano {manifest['plan_id']} · Revisión {revision['number']}"
    )
    report.line("Reporte de auditoría del paquete final", VALUE_FONT, TITLE_MM)
    report.heading("Manifiesto")
    report.key_values(_package_rows(manifest, case_title, revision_id, source_name, public_key_id))
    report.heading("Artefactos del paquete")
    artifacts = [
        [str(item["kind"]), str(item["filename"]), str(item["sha256"])]
        for item in manifest["artifacts"]
    ]
    report.table(ARTIFACT_COLUMNS, [*artifacts, ["pdf_audit", AUDIT_FILENAME, SELF_HASH_TEXT]])
    report.heading("Calibración")
    report.key_values(_mapping_rows(manifest["calibration"]))
    report.heading("Sistema local")
    report.key_values(_mapping_rows(manifest["local_frame"]))
    report.heading(f"Eventos del plano ({len(plan_events)})")
    report.table(
        EVENT_COLUMNS,
        [
            [
                str(event["id"]),
                str(event["kind"]),
                f"{event.get('actor_role') or NO_VALUE} {str(event.get('actor_id') or '')[:8]}",
                str(event["occurred_at"]),
                str(event["hash"])[:SHORT_HASH],
                event_summary(event),
            ]
            for event in plan_events
        ],
    )
    report.heading("Nota de validez")
    report.paragraph(VALIDITY_NOTE)
    report.heading("Alcance de la firma")
    report.paragraph(SIGNATURE_SCOPE, VALUE_FONT)
    disclaimer = (legal or ExportLegal()).disclaimer
    if disclaimer:
        report.disclaimer(disclaimer)
    report.heading("Verificación")
    for step in VERIFICATION_STEPS:
        report.paragraph(step)
    report.finish()
    return buffer.getvalue()
