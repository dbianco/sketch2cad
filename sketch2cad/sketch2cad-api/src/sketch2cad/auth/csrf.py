"""Double-submit CSRF check bound to the server-side session (decision D-8)."""

import hmac

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def requires_csrf_check(method: str) -> bool:
    """Return whether a request with ``method`` must carry a valid CSRF token."""
    return method.upper() not in SAFE_METHODS


def csrf_tokens_match(header: str | None, cookie: str | None, stored: str) -> bool:
    """Return whether the header token equals both the CSRF cookie and the session's token.

    Every comparison runs in constant time; a missing header or cookie never matches.
    """
    if not header or not cookie or not stored:
        return False
    header_bytes = header.encode("utf-8")
    cookie_ok = hmac.compare_digest(header_bytes, cookie.encode("utf-8"))
    stored_ok = hmac.compare_digest(header_bytes, stored.encode("utf-8"))
    return cookie_ok and stored_ok
