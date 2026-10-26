"""Legal disclaimer of final exports (spec 13.2 and decisions, REQ-14, DEC-8).

Until the legal review of the signature and the chain of custody (DEC-8), the final
technical PDF and the audit report print :data:`DISCLAIMER_TEXT`. The setting
``legal.disclaimer_enabled`` (environment variable ``LEGAL_DISCLAIMER_ENABLED``, default
true) turns it off. Drafts do not print it: their ``BORRADOR / DRAFT`` watermark
(:mod:`sketch2cad.export.pdf_watermark`) already says the plan is not validated. The DXF
does not carry it either (the spec asks for it only in the PDF and the audit report).

:class:`ExportLegal` keeps the exporters pure: callers build it once with
:meth:`ExportLegal.from_settings` (API) or :meth:`ExportLegal.from_environment` (worker) and
pass it down. :func:`draw_disclaimer` draws the sentence in a framed band of a ReportLab
canvas, wrapped to its width; :func:`disclaimer_band_height` says how tall that band is.
"""

# The ReportLab stubs leave canvas callbacks and ``simpleSplit`` partly unannotated (Unknown).
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from dataclasses import dataclass
from typing import Any

from pydantic import create_model
from pydantic_settings import BaseSettings, SettingsConfigDict
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from sketch2cad.config import Settings
from sketch2cad.export.pdf_sheet import VALUE_FONT, Rect, mm_to_pt

DISCLAIMER_TEXT = "Borrador técnico, no apto como prueba hasta contar con revisión jurídica"
DISCLAIMER_SETTING = "LEGAL_DISCLAIMER_ENABLED"
DISCLAIMER_FONT = VALUE_FONT
DISCLAIMER_TEXT_MM = 2.2
DISCLAIMER_LEADING_MM = 3.0
DISCLAIMER_PADDING_MM = 1.2
DISCLAIMER_LINE_MM = 0.25


def disclaimer_text(enabled: bool) -> str | None:
    """:data:`DISCLAIMER_TEXT` when ``legal.disclaimer_enabled`` is true, else ``None``."""
    return DISCLAIMER_TEXT if enabled else None


@dataclass(frozen=True)
class ExportLegal:
    """Legal options of the exports; the default matches :class:`~sketch2cad.config.Settings`."""

    disclaimer_enabled: bool = True

    @property
    def disclaimer(self) -> str | None:
        """The sentence final exports print, or ``None`` when it is disabled."""
        return disclaimer_text(self.disclaimer_enabled)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ExportLegal":
        """Read ``LEGAL_DISCLAIMER_ENABLED`` of the API settings."""
        return cls(disclaimer_enabled=settings.LEGAL_DISCLAIMER_ENABLED)

    @classmethod
    def from_environment(cls) -> "ExportLegal":
        """Read only ``LEGAL_DISCLAIMER_ENABLED`` (default true when unset).

        Raises ``pydantic.ValidationError`` when the variable is not a boolean.
        """
        values: dict[str, Any] = _LegalEnvironment().model_dump()
        return cls(disclaimer_enabled=values[DISCLAIMER_SETTING])


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")


# Same type and default as the API settings field.
_LEGAL_FIELDS: dict[str, Any] = {
    DISCLAIMER_SETTING: (
        Settings.model_fields[DISCLAIMER_SETTING].annotation,
        Settings.model_fields[DISCLAIMER_SETTING],
    )
}
_LegalEnvironment = create_model(
    "_LegalEnvironment", __base__=_EnvironmentSettings, **_LEGAL_FIELDS
)


def disclaimer_lines(text: str, width_mm: float) -> list[str]:
    """``text`` wrapped at spaces to fit a band ``width_mm`` wide."""
    room = mm_to_pt(width_mm - 2 * DISCLAIMER_PADDING_MM)
    return [
        str(line) for line in simpleSplit(text, DISCLAIMER_FONT, mm_to_pt(DISCLAIMER_TEXT_MM), room)
    ]


def disclaimer_band_height(text: str, width_mm: float) -> float:
    """Height in mm of the framed band that :func:`draw_disclaimer` draws."""
    return len(disclaimer_lines(text, width_mm)) * DISCLAIMER_LEADING_MM + 2 * DISCLAIMER_PADDING_MM


def draw_disclaimer(canvas: Canvas, area: Rect, text: str) -> None:
    """``text`` in bold black inside a thin frame, from the top of ``area`` downwards."""
    lines = disclaimer_lines(text, area.width)
    height = len(lines) * DISCLAIMER_LEADING_MM + 2 * DISCLAIMER_PADDING_MM
    canvas.saveState()
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(mm_to_pt(DISCLAIMER_LINE_MM))
    bottom = area.top - height
    canvas.rect(*(mm_to_pt(v) for v in (area.x, bottom, area.width, height)))
    canvas.setFont(DISCLAIMER_FONT, mm_to_pt(DISCLAIMER_TEXT_MM))
    for index, line in enumerate(lines):
        baseline = area.top - DISCLAIMER_PADDING_MM - (index + 1) * DISCLAIMER_LEADING_MM + 0.7
        canvas.drawString(mm_to_pt(area.x + DISCLAIMER_PADDING_MM), mm_to_pt(baseline), line)
    canvas.restoreState()
