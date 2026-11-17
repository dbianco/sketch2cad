import pytest
from pydantic import ValidationError

from sketch2cad.config import get_settings

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://user:pass@localhost:5432/sketch2cad",
    "SESSION_SECRET": "test-session-secret",
    "SIGNING_KEY_PATH": "keys/signing-key.pem",
    "APP_ENV": "test",
}


@pytest.fixture
def full_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_settings_are_read_from_environment(full_env: pytest.MonkeyPatch) -> None:
    settings = get_settings()

    assert settings.DATABASE_URL == REQUIRED_ENV["DATABASE_URL"]
    assert settings.SESSION_SECRET.get_secret_value() == REQUIRED_ENV["SESSION_SECRET"]
    assert settings.SIGNING_KEY_PATH == REQUIRED_ENV["SIGNING_KEY_PATH"]
    assert settings.APP_ENV == REQUIRED_ENV["APP_ENV"]


def test_missing_database_url_raises_validation_error_naming_it(
    full_env: pytest.MonkeyPatch,
) -> None:
    full_env.delenv("DATABASE_URL")

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    missing = [error["loc"] for error in exc_info.value.errors() if error["type"] == "missing"]
    assert missing == [("DATABASE_URL",)]
    assert "DATABASE_URL" in str(exc_info.value)


def test_session_secret_is_not_exposed_in_repr(full_env: pytest.MonkeyPatch) -> None:
    settings = get_settings()

    assert REQUIRED_ENV["SESSION_SECRET"] not in repr(settings)
