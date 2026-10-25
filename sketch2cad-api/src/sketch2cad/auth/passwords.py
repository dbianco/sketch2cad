"""Password hashing with Argon2id using the ``argon2-cffi`` default parameters."""

import secrets
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return the Argon2id hash (PHC string) of ``password``."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return whether ``password`` matches ``password_hash``; malformed hashes never match."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return whether ``password_hash`` was produced with outdated parameters."""
    return _hasher.check_needs_rehash(password_hash)


@cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(32))


def verify_dummy_password(password: str) -> None:
    """Spend the same verification work as a real check, for usernames that do not exist.

    This keeps response times of unknown and known usernames comparable, so the login
    endpoint does not reveal which usernames exist.
    """
    verify_password(_dummy_hash(), password)
