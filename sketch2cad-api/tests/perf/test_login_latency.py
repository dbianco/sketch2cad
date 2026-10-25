"""Login latency: p95 of 20 consecutive valid logins must stay below 300 ms (H0-2).

Locally (``CI`` is not ``true``) the measurement fails when p95 >= ``P95_THRESHOLD_MS``.
In CI (``CI=true``) it never fails on latency; it appends ``login_p95_ms=<value>`` to the
file named by ``GITHUB_STEP_SUMMARY`` when that variable is set.

Only valid logins are used, so account lockout (10 failures) is never triggered.
"""

import math
import os
import time
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from sketch2cad.api import create_app
from sketch2cad.api.routers import API_V1_PREFIX
from sketch2cad.auth.passwords import hash_password
from sketch2cad.db.base import Base
from sketch2cad.db.models import User, UserRole

P95_THRESHOLD_MS = 300.0
SAMPLE_COUNT = 20
SUMMARY_KEY = "login_p95_ms"
LOGIN_URL = f"{API_V1_PREFIX}/auth/login"
PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
# Secure cookies are only sent back over HTTPS, so the client talks to an https origin.
BASE_URL = "https://testserver"


def percentile(samples: Sequence[float], pct: float) -> float:
    """Return the ``pct`` percentile of ``samples`` using the nearest-rank method.

    The result is the smallest sample such that at least ``pct`` percent of the samples are
    less than or equal to it: rank ``ceil(pct / 100 * n)`` (1-based) of the sorted samples.
    With 20 samples, p95 is rank 19, i.e. the 19th smallest value.
    """
    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0 < pct <= 100:
        raise ValueError("pct must be in the range (0, 100]")
    ordered = sorted(samples)
    rank = math.ceil(pct / 100 * len(ordered))
    return ordered[rank - 1]


def is_ci() -> bool:
    return os.environ.get("CI") == "true"


def evaluate_latency(samples_ms: Sequence[float]) -> float:
    """Compute p95 of ``samples_ms`` and apply the local or CI policy; return the p95."""
    p95 = percentile(samples_ms, 95)
    if is_ci():
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(f"{SUMMARY_KEY}={p95:.1f}\n")
        return p95
    if p95 >= P95_THRESHOLD_MS:
        pytest.fail(f"login p95 {p95:.1f} ms >= threshold {P95_THRESHOLD_MS:.0f} ms")
    return p95


@pytest.fixture(scope="module")
def tables(db_engine: Engine) -> Iterator[None]:
    """Create the application tables in the isolated schema and drop them afterwards."""
    Base.metadata.create_all(db_engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.fixture
def factory(tables: None, db_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return db_session_factory


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SIGNING_KEY_PATH", "keys/signing-key.pem")
    monkeypatch.setenv("APP_ENV", "test")
    with TestClient(create_app(), base_url=BASE_URL) as test_client:
        yield test_client


def insert_user(factory: sessionmaker[Session]) -> User:
    with factory() as db:
        user = User(
            username=f"user-{uuid.uuid4().hex[:12]}",
            display_name="Ana Pérez",
            password_hash=hash_password(PASSWORD),
            role=UserRole.TECHNICIAN,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def timed_login_ms(client: TestClient, username: str) -> float:
    start = time.perf_counter()
    response = client.post(LOGIN_URL, json={"username": username, "password": PASSWORD})
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert response.status_code == 200
    return elapsed_ms


@pytest.fixture
def local_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return monkeypatch


def test_percentile_nearest_rank_p95_of_20_is_the_19th_smallest() -> None:
    samples = [float(value) for value in range(20, 0, -1)]

    assert percentile(samples, 95) == 19.0


def test_percentile_edge_cases() -> None:
    assert percentile([42.0], 95) == 42.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert percentile([3.0, 1.0, 2.0], 100) == 3.0
    with pytest.raises(ValueError, match="at least one sample"):
        percentile([], 95)
    with pytest.raises(ValueError, match="range"):
        percentile([1.0], 0)


def test_local_mode_fails_at_or_above_threshold(local_env: pytest.MonkeyPatch) -> None:
    samples = [10.0] * 18 + [P95_THRESHOLD_MS, 1000.0]

    with pytest.raises(pytest.fail.Exception, match="threshold"):
        evaluate_latency(samples)


def test_local_mode_passes_below_threshold(local_env: pytest.MonkeyPatch) -> None:
    # The single outlier is rank 20, above p95, so it does not affect the result.
    samples = [10.0] * 18 + [P95_THRESHOLD_MS - 0.1, 5000.0]

    assert evaluate_latency(samples) == P95_THRESHOLD_MS - 0.1


@pytest.mark.parametrize("ci_value", ["false", "1", ""])
def test_ci_must_be_exactly_true_to_relax_the_threshold(
    local_env: pytest.MonkeyPatch, ci_value: str
) -> None:
    local_env.setenv("CI", ci_value)

    with pytest.raises(pytest.fail.Exception):
        evaluate_latency([1000.0] * SAMPLE_COUNT)


def test_ci_mode_never_fails_and_appends_to_step_summary(
    local_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("existing line\n", encoding="utf-8")
    local_env.setenv("CI", "true")
    local_env.setenv("GITHUB_STEP_SUMMARY", str(summary))

    p95 = evaluate_latency([10.0] * 18 + [1234.56, 9999.0])

    assert p95 == 1234.56
    assert summary.read_text(encoding="utf-8") == "existing line\nlogin_p95_ms=1234.6\n"


def test_ci_mode_without_step_summary_does_not_fail(
    local_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local_env.setenv("CI", "true")
    local_env.chdir(tmp_path)

    assert evaluate_latency([1000.0] * SAMPLE_COUNT) == 1000.0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.perf
def test_login_p95_latency(client: TestClient, factory: sessionmaker[Session]) -> None:
    user = insert_user(factory)
    timed_login_ms(client, user.username)  # Warm-up, not counted.

    samples = [timed_login_ms(client, user.username) for _ in range(SAMPLE_COUNT)]

    p95 = evaluate_latency(samples)
    print(f"{SUMMARY_KEY}={p95:.1f}")  # Visible with `pytest -s`.
