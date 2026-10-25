"""Application settings read exclusively from environment variables."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings; values come from the environment, never from code."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    DATABASE_URL: str
    SESSION_SECRET: SecretStr
    SIGNING_KEY_PATH: str
    APP_ENV: str


def get_settings() -> Settings:
    """Build the settings from the current environment."""
    return Settings()  # pyright: ignore[reportCallIssue]
