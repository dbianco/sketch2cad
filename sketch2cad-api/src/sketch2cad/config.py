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


def get_settings() -> Settings:
    """Build the settings from the current environment."""
    return Settings()  # pyright: ignore[reportCallIssue]
