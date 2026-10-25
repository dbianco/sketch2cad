"""Static checks of docker-compose.yml through ``docker compose config``."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

API_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = API_DIR / "docker-compose.yml"
SERVICES = {"db", "api", "worker", "web"}
REQUIRED_VARIABLES = ("POSTGRES_PASSWORD", "SESSION_SECRET", "SIGNING_KEY_PATH", "APP_ENV")
DUMMY_ENV = {name: f"dummy-{name.lower()}" for name in REQUIRED_VARIABLES}
SECRET_KEY = re.compile(r"password|secret", re.IGNORECASE)
VARIABLE_REFERENCE = re.compile(r"^\$\{[A-Z0-9_]+(?:(?::?[-?+]).*)?\}$")
URL_PASSWORD = re.compile(r"://[^:/@]+:(?P<password>[^@]*)@")


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(  # noqa: S603
        ["docker", "compose", "version"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_compose_available(), reason="docker compose is not available"
)


def _compose(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base_env = {
        key: value
        for key, value in os.environ.items()
        if key not in REQUIRED_VARIABLES and key != "WEB_PORT"
    }
    return subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", os.devnull, *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=API_DIR,
        env={**base_env, **env},
    )


def _config(*extra: str) -> dict[str, Any]:
    result = _compose("config", "--format", "json", *extra, env=DUMMY_ENV)
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def _services(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], config["services"])


def test_config_is_valid_with_the_required_variables() -> None:
    result = _compose("config", "-q", env=DUMMY_ENV)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("missing", REQUIRED_VARIABLES)
def test_config_fails_when_a_required_variable_is_missing(missing: str) -> None:
    env = {key: value for key, value in DUMMY_ENV.items() if key != missing}

    result = _compose("config", "-q", env=env)

    assert result.returncode != 0
    assert missing in result.stderr


def test_project_name_and_services() -> None:
    config = _config()

    assert config["name"] == "sketch2cad"
    assert set(_services(config)) == SERVICES


def test_only_web_publishes_ports() -> None:
    services = _services(_config())

    publishing = {name for name, service in services.items() if service.get("ports")}
    assert publishing == {"web"}
    ports = cast(list[dict[str, Any]], services["web"]["ports"])
    assert [(port["target"], str(port["published"])) for port in ports] == [(80, "18080")]


def test_web_port_is_configurable() -> None:
    result = _compose("config", "--format", "json", env={**DUMMY_ENV, "WEB_PORT": "28080"})

    assert result.returncode == 0, result.stderr
    config = cast(dict[str, Any], json.loads(result.stdout))
    ports = cast(list[dict[str, Any]], _services(config)["web"]["ports"])
    assert str(ports[0]["published"]) == "28080"


def test_every_service_has_a_healthcheck_or_an_image_healthcheck() -> None:
    services = _services(_config())

    # `web` declares its HEALTHCHECK in sketch2cad-web/Dockerfile.
    assert {name for name, service in services.items() if "healthcheck" in service} == {
        "db",
        "api",
        "worker",
    }
    web_dockerfile = API_DIR.parent / "sketch2cad-web" / "Dockerfile"
    assert "HEALTHCHECK" in web_dockerfile.read_text(encoding="utf-8")


def test_depends_on_waits_for_healthy_services() -> None:
    services = _services(_config())

    for name in ("api", "worker"):
        depends_on = cast(dict[str, dict[str, Any]], services[name]["depends_on"])
        assert depends_on["db"]["condition"] == "service_healthy"
    web_depends_on = cast(dict[str, dict[str, Any]], services["web"]["depends_on"])
    assert web_depends_on["api"]["condition"] == "service_healthy"


def test_secrets_are_only_variable_references() -> None:
    services = _services(_config("--no-interpolate"))

    checked = 0
    for name, service in services.items():
        environment = cast(dict[str, str | None], service.get("environment") or {})
        for key, value in environment.items():
            if SECRET_KEY.search(key):
                checked += 1
                assert value is not None, f"{name}.{key} has no value"
                assert VARIABLE_REFERENCE.match(value), f"{name}.{key} is a literal value"
            match = URL_PASSWORD.search(value or "")
            if match:
                checked += 1
                password = match.group("password")
                assert VARIABLE_REFERENCE.match(password), f"{name}.{key} embeds a password"
    assert checked >= 3


def test_no_secret_default_values() -> None:
    raw = COMPOSE_FILE.read_text(encoding="utf-8")

    for name in REQUIRED_VARIABLES:
        references = re.findall(rf"\$\{{{name}(?P<rest>[^}}]*)\}}", raw)
        assert references, f"{name} is not referenced"
        assert all(rest.startswith(":?") for rest in references), f"{name} has a default"
