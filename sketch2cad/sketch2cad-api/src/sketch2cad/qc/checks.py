"""Image quality checks of a source: short side, sharpness, contrast and paper-edge cropping.

All functions are pure and work on a decoded numpy image (grayscale, BGR or BGRA). The
checks are warnings: :func:`evaluate` never raises and always returns a :class:`QcResult`,
whose :meth:`QcResult.to_dict` is the JSON stored in ``source.qc``. Check messages are
shown to the technician in the quality semaphore, so they are written in Spanish.
"""

import logging
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt
from sqlalchemy.orm import Session

from sketch2cad.config import Settings
from sketch2cad.config_params import code_default, get_params
from sketch2cad.db.models import IncidentType

logger = logging.getLogger(__name__)

GrayImage = npt.NDArray[np.uint8]


class QcStatus(StrEnum):
    """Semaphore status of one check and of the whole result."""

    GREEN = "green"
    AMBER = "amber"
    RED = "red"


_SEVERITY = {QcStatus.GREEN: 0, QcStatus.AMBER: 1, QcStatus.RED: 2}


class CheckName(StrEnum):
    """Stable identifiers of the checks, used as ``checks[].name``."""

    INPUT = "input"
    SHORT_SIDE = "short_side"
    LAPLACIAN_VARIANCE = "laplacian_variance"
    CONTRAST = "contrast"
    PAPER_CROP = "paper_crop"


@dataclass(frozen=True)
class QcThresholds:
    """QC thresholds (spec §10 ``qc.*``); defaults are the registry code defaults."""

    min_short_side_amber: int = code_default("qc.min_short_side_amber")
    min_short_side_red: int = code_default("qc.min_short_side_red")
    min_laplacian_var_amber: float = code_default("qc.min_laplacian_var_amber")
    min_laplacian_var_red: float = code_default("qc.min_laplacian_var_red")
    min_contrast_amber: float = code_default("qc.min_contrast_amber")

    @classmethod
    def from_settings(cls, settings: Settings) -> "QcThresholds":
        """Read the thresholds from the ``QC_*`` settings."""
        return cls(
            min_short_side_amber=settings.QC_MIN_SHORT_SIDE_AMBER,
            min_short_side_red=settings.QC_MIN_SHORT_SIDE_RED,
            min_laplacian_var_amber=settings.QC_MIN_LAPLACIAN_VAR_AMBER,
            min_laplacian_var_red=settings.QC_MIN_LAPLACIAN_VAR_RED,
            min_contrast_amber=settings.QC_MIN_CONTRAST_AMBER,
        )

    @classmethod
    def resolve(cls, session: Session | None, incident_type: IncidentType | None) -> "QcThresholds":
        """Resolve every ``qc.*`` threshold (overrides, then the environment default).

        Without ``session`` only the ``QC_*`` environment variables apply; raises
        ``pydantic.ValidationError`` when one breaks the settings constraints.
        """
        names = [f"qc.{f.name}" for f in fields(cls)]
        v = get_params(names, incident_type=incident_type, session=session)
        return cls(
            min_short_side_amber=int(v["qc.min_short_side_amber"]),
            min_short_side_red=int(v["qc.min_short_side_red"]),
            min_laplacian_var_amber=float(v["qc.min_laplacian_var_amber"]),
            min_laplacian_var_red=float(v["qc.min_laplacian_var_red"]),
            min_contrast_amber=float(v["qc.min_contrast_amber"]),
        )


@dataclass(frozen=True)
class QcCheck:
    """Outcome of one check; ``threshold`` is the limit that decided ``status``.

    An amber or red check also says why (``cause``) and what the technician can do
    (``action``), in Spanish (REQ-15); both are ``None`` for a green check.
    """

    name: CheckName
    status: QcStatus
    value: float | None
    threshold: float | None
    message: str
    cause: str | None = None
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON form of the check (``source.qc.checks[]``)."""
        return {
            "name": self.name.value,
            "status": self.status.value,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "cause": self.cause,
            "action": self.action,
        }


@dataclass(frozen=True)
class QcResult:
    """Overall semaphore (worst check status) and the individual checks."""

    status: QcStatus
    checks: tuple[QcCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON form stored in ``source.qc``."""
        return {"status": self.status.value, "checks": [c.to_dict() for c in self.checks]}


def _graded(value: float, red: float | None, amber: float) -> tuple[QcStatus, float]:
    """Status and deciding threshold for a "higher is better" value."""
    if red is not None and value < red:
        return QcStatus.RED, red
    if value < amber:
        return QcStatus.AMBER, amber
    return QcStatus.GREEN, amber


def _guidance(
    status: QcStatus, cause: str, actions: dict[QcStatus, str], **values: object
) -> tuple[str | None, str | None]:
    """``(cause, action)`` of a check: ``(None, None)`` when green, else both filled in."""
    if status is QcStatus.GREEN:
        return None, None
    return cause.format(**values), actions[status].format(**values)


def to_gray(image: npt.NDArray[Any]) -> GrayImage:
    """Convert a grayscale, BGR or BGRA image of any integer or float depth to 8-bit gray.

    Raises ``ValueError`` for shapes that are not an image; :func:`evaluate` reports it.
    """
    if image.ndim == 3 and image.shape[2] == 1:
        image = image[:, :, 0]
    if image.ndim not in (2, 3) or image.size == 0:
        raise ValueError(f"unsupported image shape {image.shape}")
    eight_bit = _to_uint8(image)
    if eight_bit.ndim == 2:
        return eight_bit
    channels = int(eight_bit.shape[2])
    if channels == 3:
        return cv2.cvtColor(eight_bit, cv2.COLOR_BGR2GRAY).astype(np.uint8)
    if channels == 4:
        return cv2.cvtColor(eight_bit, cv2.COLOR_BGRA2GRAY).astype(np.uint8)
    raise ValueError(f"unsupported channel count {channels}")


def _to_uint8(image: npt.NDArray[Any]) -> GrayImage:
    """8-bit version of ``image``: uint16 keeps its high byte, other numbers are stretched."""
    if image.dtype == np.uint8:
        return image
    if image.dtype == np.uint16:
        return (image >> 8).astype(np.uint8)
    if not (np.issubdtype(image.dtype, np.integer) or np.issubdtype(image.dtype, np.floating)):
        raise ValueError(f"unsupported image dtype {image.dtype}")
    values = np.nan_to_num(image.astype(np.float64))
    low, high = float(values.min()), float(values.max())
    if high <= low:
        return np.zeros(image.shape, dtype=np.uint8)
    return ((values - low) * (255.0 / (high - low))).round().astype(np.uint8)


def short_side(gray: GrayImage) -> int:
    """Length in pixels of the shorter image side."""
    return int(min(gray.shape[0], gray.shape[1]))


def laplacian_variance(gray: GrayImage) -> float:
    """Variance of the Laplacian of the grayscale image (low values mean blur)."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def contrast_range(gray: GrayImage) -> float:
    """Gray-level spread between the 5th and 95th percentiles (p95 - p5)."""
    p5, p95 = np.percentile(gray, [5, 95])
    return float(p95 - p5)


def paper_border_fraction(gray: GrayImage) -> float:
    """Fraction of the outer one-pixel ring of the image covered by the sheet of paper.

    The sheet is the largest bright connected region after an Otsu threshold. ``0`` means
    the sheet lies fully inside the frame, ``1`` that it fills the whole frame (a scan or
    a tight photo) and anything in between that the frame cuts the sheet.
    """
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return 0.0
    paper_label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    paper = labels == paper_label
    ring = np.concatenate([paper[0, :], paper[-1, :], paper[1:-1, 0], paper[1:-1, -1]])
    return float(ring.mean())


def _short_side_check(gray: GrayImage, t: QcThresholds) -> QcCheck:
    value = short_side(gray)
    status, threshold = _graded(value, t.min_short_side_red, t.min_short_side_amber)
    messages = {
        QcStatus.RED: "Resolución insuficiente: el lado corto mide {v} px (mínimo {t} px).",
        QcStatus.AMBER: "Resolución baja: el lado corto mide {v} px (recomendado {t} px).",
        QcStatus.GREEN: "Resolución adecuada: el lado corto mide {v} px.",
    }
    message = messages[status].format(v=value, t=int(threshold))
    actions = {
        QcStatus.RED: "Vuelve a fotografiar o escanear el croquis con al menos {t} px en el "
        "lado corto.",
        QcStatus.AMBER: "Si puedes, usa una foto o un escaneo de mayor resolución.",
    }
    cause, action = _guidance(status, message, actions, t=int(threshold))
    return QcCheck(
        CheckName.SHORT_SIDE, status, float(value), float(threshold), message, cause, action
    )


def _sharpness_check(gray: GrayImage, t: QcThresholds) -> QcCheck:
    value = round(laplacian_variance(gray), 2)
    status, threshold = _graded(value, t.min_laplacian_var_red, t.min_laplacian_var_amber)
    messages = {
        QcStatus.RED: "Imagen muy borrosa: vuelve a fotografiar el croquis enfocado.",
        QcStatus.AMBER: "Imagen algo borrosa: algunos trazos o cotas pueden no leerse.",
        QcStatus.GREEN: "Nitidez adecuada.",
    }
    causes = {
        QcStatus.RED: "Imagen muy borrosa: la varianza del Laplaciano es {v} (mínimo {t}).",
        QcStatus.AMBER: "Imagen algo borrosa: algunos trazos o cotas pueden no leerse.",
    }
    actions = {
        QcStatus.RED: "Vuelve a fotografiar el croquis enfocado, con buena luz y sin moverte.",
        QcStatus.AMBER: "Si puedes, vuelve a fotografiarlo enfocado; si no, revisa las cotas "
        "a mano.",
    }
    shown = {"v": f"{value:.1f}".replace(".", ","), "t": f"{threshold:g}".replace(".", ",")}
    cause, action = _guidance(status, causes.get(status, ""), actions, **shown)
    return QcCheck(
        CheckName.LAPLACIAN_VARIANCE, status, value, threshold, messages[status], cause, action
    )


def _contrast_check(gray: GrayImage, t: QcThresholds) -> QcCheck:
    value = round(contrast_range(gray), 2)
    status, threshold = _graded(value, None, t.min_contrast_amber)
    messages = {
        QcStatus.AMBER: "Contraste bajo: mejora la iluminación o usa un fondo más oscuro.",
        QcStatus.GREEN: "Contraste adecuado.",
    }
    actions = {
        QcStatus.AMBER: "Vuelve a fotografiar el croquis con más luz o sobre un fondo más oscuro.",
    }
    cause, action = _guidance(status, "Contraste bajo entre el papel y los trazos.", actions)
    return QcCheck(CheckName.CONTRAST, status, value, threshold, messages[status], cause, action)


def _crop_check(gray: GrayImage) -> QcCheck:
    value = round(paper_border_fraction(gray), 4)
    if 0.0 < value < 1.0:
        message = "El papel toca el borde de la imagen: puede faltar parte del croquis."
        action = "Vuelve a fotografiar el croquis con todo el papel dentro de la imagen."
        return QcCheck(CheckName.PAPER_CROP, QcStatus.AMBER, value, None, message, message, action)
    message = "No se detecta el papel cortado por el borde de la imagen."
    return QcCheck(CheckName.PAPER_CROP, QcStatus.GREEN, value, None, message)


def _unreadable(reason: str) -> QcResult:
    message = "No se pudo analizar la imagen: vuelve a cargar el archivo."
    cause = "No se pudo leer ni analizar la imagen de la fuente."
    action = "Vuelve a cargar el archivo original o una nueva foto o escaneo del croquis."
    check = QcCheck(CheckName.INPUT, QcStatus.RED, None, None, message, cause, action)
    logger.warning("QC could not analyze the image: %s", reason)
    return QcResult(QcStatus.RED, (check,))


def evaluate(image: object, thresholds: QcThresholds | None = None) -> QcResult:
    """Run every quality check on ``image`` and return the semaphore with its checks.

    ``thresholds`` defaults to :class:`QcThresholds` defaults; callers with settings pass
    ``QcThresholds.from_settings(settings)``. Never raises: input that is not a usable
    image yields a red result with a single ``input`` check.
    """
    t = thresholds or QcThresholds()
    if not isinstance(image, np.ndarray):
        return _unreadable(f"expected a numpy array, got {type(image).__name__}")
    try:
        gray = to_gray(cast(npt.NDArray[Any], image))
        checks = (
            _short_side_check(gray, t),
            _sharpness_check(gray, t),
            _contrast_check(gray, t),
            _crop_check(gray),
        )
    except Exception as exc:  # the QC is a warning: no input may make it raise
        return _unreadable(type(exc).__name__)  # messages may quote array content
    status = max((c.status for c in checks), key=_SEVERITY.__getitem__)
    return QcResult(status, checks)
