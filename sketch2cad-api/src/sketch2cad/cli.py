"""Administrative command line: ``python -m sketch2cad.cli <command>``.

Commands:

- ``create-user --username NAME --role admin|technician|reviewer [--display-name NAME]``
  reads the password from stdin (first line; prompted twice when stdin is a terminal),
  hashes it with Argon2id and inserts an active user.
- ``generate-signing-key [--path PATH] [--if-missing]`` creates the Ed25519 signing key
  file (mode 0600) at ``PATH`` (default ``$SIGNING_KEY_PATH``) and prints its public key
  id and public key; it never overwrites a file and never prints the private key. With
  ``--if-missing`` an existing valid key file is kept (exit 0).

Exit status: 0 on success, 1 on runtime errors (duplicate username, empty password,
missing ``DATABASE_URL``, database failure, existing or unsafe key file), 2 on usage
errors (argparse).

``create-user`` reads only ``DATABASE_URL`` and ``generate-signing-key`` only
``SIGNING_KEY_PATH`` from the environment; the full application settings are
not required. Connection parameters such as ``options=-csearch_path=...`` may be given
in the URL query string.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any, TextIO

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.models import User, UserRole
from sketch2cad.db.session import create_db_engine, create_session_factory
from sketch2cad.package.signing import (
    SIGNING_KEY_ENV,
    SigningKeyError,
    encode_public_key,
    generate_signing_key,
    load_signing_key,
    public_key_id,
)

EXIT_OK = 0
EXIT_ERROR = 1
STDIN_ONLY_ERROR = (
    "--password is not accepted: passwords are read from stdin only "
    "(pipe it in or type it at the prompt)"
)

ReadSecret = Callable[[str], str]


class CliError(Exception):
    """A runtime failure reported to the operator with exit status 1."""


class _RejectPasswordAction(argparse.Action):
    """Hidden ``--password`` option that always fails, without echoing its value."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        parser.error(STDIN_ONLY_ERROR)


def _add_rejected_password_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--password", nargs="?", action=_RejectPasswordAction, help=argparse.SUPPRESS
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser; each command is a subparser."""
    parser = argparse.ArgumentParser(prog="python -m sketch2cad.cli", description=__doc__)
    _add_rejected_password_option(parser)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    create_user = commands.add_parser(
        "create-user",
        help="create a user; the password is read from stdin",
        description="Create an active user. The password is read from stdin only.",
    )
    create_user.add_argument("--username", required=True)
    create_user.add_argument("--role", required=True, choices=[role.value for role in UserRole])
    create_user.add_argument("--display-name", default=None)
    _add_rejected_password_option(create_user)

    signing_key = commands.add_parser(
        "generate-signing-key",
        help="create the Ed25519 signing key file (mode 0600)",
        description="Create the Ed25519 signing key file with mode 0600. Never overwrites.",
    )
    signing_key.add_argument(
        "--path", default=None, help=f"key file path (default: ${SIGNING_KEY_ENV})"
    )
    signing_key.add_argument(
        "--if-missing",
        action="store_true",
        help="keep an existing valid key file instead of failing",
    )
    return parser


def read_password(stdin: TextIO, read_secret: ReadSecret) -> str:
    """Return the password from stdin; prompt twice (no echo) when stdin is a terminal."""
    if stdin.isatty():
        password = read_secret("Password: ")
        if read_secret("Repeat password: ") != password:
            raise CliError("passwords do not match")
    else:
        password = stdin.readline().removesuffix("\n").removesuffix("\r")
    if not password:
        raise CliError("the password must not be empty")
    return password


def create_user(
    session_factory: sessionmaker[Session],
    username: str,
    role: UserRole,
    display_name: str | None,
    password: str,
) -> User:
    """Insert an active user with an Argon2id hash of ``password``.

    Raises ``CliError`` when the username already exists; nothing is written then.
    """
    duplicate = CliError(f"a user named {username!r} already exists")
    with session_factory() as db:
        if db.scalar(select(User.id).where(User.username == username)) is not None:
            raise duplicate
        user = User(
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise duplicate from None
        return user


def _engine_from_environment() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise CliError("DATABASE_URL is not set")
    return create_db_engine(database_url)


def _create_user_command(
    args: argparse.Namespace,
    stdin: TextIO,
    session_factory: sessionmaker[Session] | None,
    read_secret: ReadSecret,
) -> int:
    username = str(args.username).strip()
    if not username:
        raise CliError("the username must not be empty")
    role = UserRole(args.role)
    display_name: str | None = args.display_name
    # Resolve the database before asking for the password, so a missing DATABASE_URL
    # fails fast instead of after the operator typed it.
    engine: Engine | None = None
    if session_factory is None:
        engine = _engine_from_environment()
        session_factory = create_session_factory(engine)
    factory = session_factory
    try:
        password = read_password(stdin, read_secret)
        user = create_user(factory, username, role, display_name, password)
    except SQLAlchemyError as exc:
        # Only the exception type: driver messages may contain connection details.
        raise CliError(f"database error ({type(exc).__name__})") from None
    finally:
        if engine is not None:
            engine.dispose()
    print(f"created user {user.username!r} with role {user.role.value}")
    return EXIT_OK


def _generate_signing_key_command(args: argparse.Namespace) -> int:
    path: str = args.path or os.environ.get(SIGNING_KEY_ENV, "")
    if not path:
        raise CliError(f"no key path: pass --path or set {SIGNING_KEY_ENV}")
    try:
        if args.if_missing and (os.path.exists(path) or os.path.islink(path)):
            key = load_signing_key(path)
            action = "kept existing"
        else:
            key = generate_signing_key(path)
            action = "created"
    except SigningKeyError as exc:
        raise CliError(str(exc)) from None
    except OSError as exc:
        raise CliError(
            f"cannot write signing key file {path} ({exc.strerror or type(exc).__name__})"
        ) from None
    public_key = key.public_key()
    print(f"{action} signing key file {path}")
    print(f"public_key_id={public_key_id(public_key)}")
    print(f"public_key={encode_public_key(public_key)}")
    return EXIT_OK


def run(
    argv: Sequence[str],
    stdin: TextIO,
    session_factory: sessionmaker[Session] | None = None,
    read_secret: ReadSecret = getpass.getpass,
) -> int:
    """Run the CLI and return its exit status.

    ``session_factory`` defaults to one built from ``DATABASE_URL``; ``read_secret`` is
    the no-echo prompt used when ``stdin`` is a terminal.
    """
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_ERROR
    try:
        if args.command == "create-user":
            return _create_user_command(args, stdin, session_factory, read_secret)
        if args.command == "generate-signing-key":
            return _generate_signing_key_command(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    raise AssertionError(f"unhandled command {args.command!r}")  # pragma: no cover


def main() -> None:
    """Console entry point."""
    sys.exit(run(sys.argv[1:], sys.stdin))


if __name__ == "__main__":
    main()
