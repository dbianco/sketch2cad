"""Application settings read exclusively from environment variables."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings; values come from the environment, never from code."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

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


def get_settings() -> Settings:
    """Build the settings from the current environment."""
    return Settings()  # pyright: ignore[reportCallIssue]
