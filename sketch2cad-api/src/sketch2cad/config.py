"""Application settings read exclusively from environment variables.

The fields named after a configuration parameter (``qc.*``, ``calibration.*``, ...) are the
environment/code-default layer of :mod:`sketch2cad.config_params`, which adds the global and
per-incident-type overrides stored in the database (REQ-19).
"""

from typing import Annotated

from pydantic import AfterValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _odd(value: int) -> int:
    if value % 2 == 0:
        raise ValueError("must be odd")
    return value


class Settings(BaseSettings):
    """Runtime settings; values come from the environment, never from code."""

    model_config = SettingsConfigDict(
        case_sensitive=True, extra="ignore", use_attribute_docstrings=True
    )

    DATABASE_URL: str
    SESSION_SECRET: SecretStr
    SIGNING_KEY_PATH: str
    APP_ENV: str
    AUTH_SESSION_TTL_MINUTES: int = Field(default=480, gt=0)
    """Session lifetime (``auth.session_ttl_minutes``); optional, defaults to 8 hours."""
    AUTH_LOCKOUT_MINUTES: int = Field(default=15, gt=0)
    """Account lock duration after too many failed logins (``auth.lockout_minutes``)."""
    STORAGE_ROOT: str = Field(default="storage", min_length=1)
    """Root directory of the filesystem object storage (holds ``objects/`` and ``tmp/``).

    Both subdirectories must live on one filesystem, since objects are published with a
    hard link. Relative paths resolve against the working directory (``/app`` in the image).
    """
    QC_MIN_SHORT_SIDE_AMBER: int = Field(default=1500, gt=0)
    """Short image side in px below which QC is amber (``qc.min_short_side_amber``)."""
    QC_MIN_SHORT_SIDE_RED: int = Field(default=900, gt=0)
    """Short image side in px below which QC is red (``qc.min_short_side_red``)."""
    QC_MIN_LAPLACIAN_VAR_AMBER: float = Field(default=100.0, gt=0)
    """Laplacian variance below which QC is amber (``qc.min_laplacian_var_amber``)."""
    QC_MIN_LAPLACIAN_VAR_RED: float = Field(default=40.0, gt=0)
    """Laplacian variance below which QC is red (``qc.min_laplacian_var_red``)."""
    QC_MIN_CONTRAST_AMBER: float = Field(default=60.0, gt=0)
    """Gray-level spread p95 - p5 below which QC is amber (``qc.min_contrast_amber``)."""
    CALIBRATION_MAX_REPROJECTION_ERROR_MM: float = Field(default=1.0, gt=0)
    """Largest reprojection error accepted as metric (``calibration.max_reprojection_error_mm``)."""
    CALIBRATION_MAX_SCALE_DISCREPANCY_PCT: float = Field(default=1.0, gt=0)
    """Largest scale discrepancy within tolerance (``calibration.max_scale_discrepancy_pct``)."""
    DIMENSION_DISCREPANCY_TOLERANCE_M: float = Field(default=0.02, gt=0)
    """Declared vs measured difference above which a dimension is flagged, in metres
    (``dimension.discrepancy_tolerance_m``)."""
    LEGAL_DISCLAIMER_ENABLED: bool = True
    """Whether the final PDF and the audit report print the legal disclaimer
    (``legal.disclaimer_enabled``); true until the legal review (DEC-8)."""
    EXPORT_CONTROL_DISTANCE_TOLERANCE_PCT: float = Field(default=0.5, gt=0)
    """Control distance tolerance of the scale test (``export.control_distance_tolerance_pct``)."""
    SUGGESTIONS_MIN_CONFIDENCE_HIGHLIGHT: float = Field(default=0.7, ge=0, le=1)
    """Confidence below which suggestions are highlighted
    (``suggestions.min_confidence_highlight``)."""
    SUGGEST_LINES_THRESHOLD_BLOCK_SIZE: Annotated[int, AfterValidator(_odd)] = Field(
        default=31, gt=1
    )
    """Odd block size in px of the adaptive threshold (``suggest_lines.threshold_block_size``)."""
    SUGGEST_LINES_THRESHOLD_C: float = Field(default=10.0, allow_inf_nan=False)
    """Constant subtracted by the adaptive threshold (``suggest_lines.threshold_c``)."""
    SUGGEST_LINES_OPENING_KERNEL_PX: int = Field(default=3, gt=0)
    """Side in px of the morphological opening kernel (``suggest_lines.opening_kernel_px``)."""
    SUGGEST_LINES_SIMPLIFY_TOLERANCE_MM: float = Field(default=0.8, gt=0)
    """Paper mm tolerance of the polyline simplification
    (``suggest_lines.simplify_tolerance_mm``)."""
    SUGGEST_LINES_MIN_LENGTH_MM: float = Field(default=5.0, gt=0)
    """Paper mm below which a path is discarded (``suggest_lines.min_length_mm``)."""
    SUGGEST_LINES_MERGE_ANGLE_DEG: float = Field(default=5.0, gt=0, lt=90)
    """Angle below which collinear paths are merged (``suggest_lines.merge_angle_deg``)."""
    SUGGEST_LINES_JOIN_GAP_MM: float = Field(default=2.0, ge=0)
    """Paper mm up to which the ends of two collinear fragments are joined across a gap or an
    overlap (``suggest_lines.join_gap_mm``); 0 joins only touching ends."""
    SUGGEST_LINES_JOIN_OFFSET_MM: float = Field(default=1.0, ge=0)
    """Paper mm of lateral offset allowed when joining fragments and when straightening
    small kinks (``suggest_lines.join_offset_mm``); 0 disables straightening."""
    SUGGEST_LINES_REMOVE_GRID: bool = True
    """Whether light coloured grid lines (graph paper) are removed from colour images before
    thresholding (``suggest_lines.remove_grid``); no effect on gray images."""
    OCR_MIN_CONFIDENCE: float = Field(default=0.6, ge=0, le=1)
    """OCR confidence needed to preselect a value (``ocr.min_confidence``)."""
    AI_ASSIST_ENABLED: bool = True
    """Whether the AI jobs ``suggest_lines`` and ``ocr_crop`` run (``ai_assist.enabled``)."""


def get_settings() -> Settings:
    """Build the settings from the current environment."""
    return Settings()  # pyright: ignore[reportCallIssue]
