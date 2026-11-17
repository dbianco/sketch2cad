"""The ``create-user`` command of ``python -m sketch2cad.cli``."""

import io
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.auth.passwords import verify_password
from sketch2cad.cli import STDIN_ONLY_ERROR, run
from sketch2cad.db.base import Base
from sketch2cad.db.models import User, UserRole

PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
API_ROOT = Path(__file__).resolve().parents[1]


class FakeTty(io.StringIO):
    """A stdin that claims to be a terminal, so the CLI prompts instead of reading it."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
    # Pooled connections may hold prepared plans for tables another module dropped.
    db_engine.dispose()
    Base.metadata.create_all(db_engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.fixture
def factory(tables: None, db_engine: Engine) -> Iterator[sessionmaker[Session]]:
    session_factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    yield session_factory
    with session_factory() as db:
        db.query(User).delete()
        db.commit()


def new_username() -> str:
    return f"user-{uuid.uuid4().hex[:12]}"


def create_args(username: str, role: str = "technician") -> list[str]:
    return ["create-user", "--username", username, "--role", role]


def users_named(factory: sessionmaker[Session], username: str) -> list[User]:
    with factory() as db:
        return list(db.scalars(select(User).where(User.username == username)))


def count_users(factory: sessionmaker[Session]) -> int:
    with factory() as db:
        return db.scalar(select(func.count()).select_from(User)) or 0


def assert_no_secrets(text: str, factory: sessionmaker[Session]) -> None:
    assert PASSWORD not in text
    assert "$argon2" not in text
    with factory() as db:
        for password_hash in db.scalars(select(User.password_hash)):
            assert password_hash not in text


def test_creates_active_user_with_argon2id_hash(
    factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    username = new_username()
    argv = [*create_args(username, "admin"), "--display-name", "Ana Pérez"]

    status = run(argv, io.StringIO(f"{PASSWORD}\n"), factory)

    assert status == 0
    [user] = users_named(factory, username)
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(user.password_hash, PASSWORD)
    assert user.role is UserRole.ADMIN
    assert user.display_name == "Ana Pérez"
    assert user.is_active is True
    output = capsys.readouterr()
    assert username in output.out
    assert_no_secrets(output.out + output.err, factory)


def test_only_the_trailing_newline_is_stripped(factory: sessionmaker[Session]) -> None:
    username = new_username()

    status = run(create_args(username), io.StringIO(" spaced pass \r\nsecond line\n"), factory)

    assert status == 0
    [user] = users_named(factory, username)
    assert verify_password(user.password_hash, " spaced pass ")
    assert user.display_name is None
    assert user.role is UserRole.TECHNICIAN


def test_password_without_trailing_newline_is_accepted(factory: sessionmaker[Session]) -> None:
    username = new_username()

    assert run(create_args(username, "reviewer"), io.StringIO(PASSWORD), factory) == 0

    [user] = users_named(factory, username)
    assert user.role is UserRole.REVIEWER
    assert verify_password(user.password_hash, PASSWORD)


@pytest.mark.parametrize(
    "extra",
    [["--password", PASSWORD], [f"--password={PASSWORD}"], ["--password"]],
    ids=["separate-value", "inline-value", "no-value"],
)
def test_password_argument_is_rejected_with_status_2(
    extra: list[str], factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    username = new_username()

    status = run([*create_args(username), *extra], io.StringIO(f"{PASSWORD}\n"), factory)

    assert status == 2
    output = capsys.readouterr()
    assert STDIN_ONLY_ERROR in output.err
    assert "stdin" in output.err
    assert PASSWORD not in output.out + output.err
    assert users_named(factory, username) == []


def test_password_argument_before_the_command_is_rejected(
    factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    status = run(["--password", PASSWORD, *create_args(new_username())], io.StringIO(), factory)

    assert status == 2
    output = capsys.readouterr()
    assert STDIN_ONLY_ERROR in output.err
    assert PASSWORD not in output.err


def test_duplicate_username_exits_1_without_modifying_the_table(
    factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    username = new_username()
    assert run(create_args(username, "admin"), io.StringIO(f"{PASSWORD}\n"), factory) == 0
    [original] = users_named(factory, username)
    total = count_users(factory)
    capsys.readouterr()

    status = run(create_args(username, "reviewer"), io.StringIO("other password\n"), factory)

    assert status == 1
    [unchanged] = users_named(factory, username)
    assert unchanged.id == original.id
    assert unchanged.password_hash == original.password_hash
    assert unchanged.role is UserRole.ADMIN
    assert count_users(factory) == total
    output = capsys.readouterr()
    assert "already exists" in output.err
    assert "other password" not in output.out + output.err
    assert_no_secrets(output.out + output.err, factory)


def test_invalid_role_exits_2(
    factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    username = new_username()

    status = run(create_args(username, "superuser"), io.StringIO(f"{PASSWORD}\n"), factory)

    assert status == 2
    assert "invalid choice" in capsys.readouterr().err
    assert users_named(factory, username) == []


@pytest.mark.parametrize("stdin_text", ["", "\n", "\r\n"], ids=["eof", "newline", "crlf"])
def test_empty_password_exits_1(
    stdin_text: str, factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    username = new_username()

    status = run(create_args(username), io.StringIO(stdin_text), factory)

    assert status == 1
    assert "password must not be empty" in capsys.readouterr().err
    assert users_named(factory, username) == []


def test_empty_username_exits_1(
    factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    status = run(create_args("   "), io.StringIO(f"{PASSWORD}\n"), factory)

    assert status == 1
    assert "username must not be empty" in capsys.readouterr().err


def test_terminal_prompts_twice_without_reading_stdin(factory: sessionmaker[Session]) -> None:
    username = new_username()
    prompts: list[str] = []

    def read_secret(prompt: str) -> str:
        prompts.append(prompt)
        return PASSWORD

    status = run(create_args(username), FakeTty("ignored\n"), factory, read_secret)

    assert status == 0
    assert prompts == ["Password: ", "Repeat password: "]
    [user] = users_named(factory, username)
    assert verify_password(user.password_hash, PASSWORD)


def test_terminal_confirmation_mismatch_exits_1(
    factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    username = new_username()
    answers = iter([PASSWORD, "something else"])

    status = run(create_args(username), FakeTty(), factory, lambda _prompt: next(answers))

    assert status == 1
    output = capsys.readouterr()
    assert "passwords do not match" in output.err
    assert PASSWORD not in output.out + output.err
    assert users_named(factory, username) == []


def test_missing_database_url_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    status = run(create_args(new_username()), io.StringIO(f"{PASSWORD}\n"))

    assert status == 1
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def run_module(args: list[str], stdin: str, database_url: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m sketch2cad.cli`` with only ``DATABASE_URL`` configured."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"SESSION_SECRET", "SIGNING_KEY_PATH", "APP_ENV"}
    }
    env["DATABASE_URL"] = database_url
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "sketch2cad.cli", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env=env,
        timeout=60,
        check=False,
    )


@pytest.fixture
def schema_database_url(database_url: str, test_schema: str) -> str:
    """``DATABASE_URL`` whose connections use the isolated schema via ``options``."""
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={test_schema}"})
    return url.render_as_string(hide_password=False)


def test_module_entry_point_creates_user(
    factory: sessionmaker[Session], schema_database_url: str
) -> None:
    username = new_username()

    result = run_module(create_args(username, "admin"), f"{PASSWORD}\n", schema_database_url)

    assert result.returncode == 0, result.stderr
    [user] = users_named(factory, username)
    assert user.password_hash.startswith("$argon2id$")
    assert_no_secrets(result.stdout + result.stderr, factory)


def test_module_entry_point_rejects_password_argument(
    factory: sessionmaker[Session], schema_database_url: str
) -> None:
    username = new_username()

    result = run_module([*create_args(username), "--password", PASSWORD], "", schema_database_url)

    assert result.returncode == 2
    assert "passwords are read from stdin only" in result.stderr
    assert PASSWORD not in result.stdout + result.stderr
    assert users_named(factory, username) == []
