"""Tests for the test-environment safety guard in ``conftest.py``.

The guard (``_guard_test_env``) prevents the test session from running when
``DATABASE_URL`` points at a production database or ``QUEUE_NAMESPACE`` is the
production default. These tests call the guard directly with patched
environment variables so they don't depend on the session-scoped
``_create_schema`` fixture (which runs the guard exactly once with the safe
config).

Covered cases:

- Safe config (test DB + test namespace) → no exception.
- Production DB name ``job_search_agent`` → RuntimeError.
- Non-test DB name without allowlist → RuntimeError.
- Production queue namespace → RuntimeError.
- ``APP_ENV`` not ``test`` → RuntimeError.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from unittest import mock

import pytest

# The guard module is ``app.tests.conftest``. Import it lazily inside tests so
# patching ``os.environ`` takes effect cleanly.


@pytest.fixture()
def _preserve_env() -> Iterator[None]:
    """Snapshot and restore ``os.environ`` so tests can mutate it freely."""
    snapshot = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(snapshot)


def _reload_conftest() -> object:
    """Reload the conftest module so it picks up the current ``os.environ``.

    The module-level ``os.environ[...] = ...`` assignments run on import, but
    the guard itself reads ``os.environ`` at call time, so a reload is not
    strictly required for the guard. We reload anyway to keep the module's
    cached state consistent with the patched env in case future code reads
    module-level constants.
    """
    import app.tests.conftest as mod

    return importlib.reload(mod)


def test_guard_passes_with_safe_test_config(_preserve_env: None) -> None:
    """Safe test DB + test namespace + APP_ENV=test → no exception."""
    mod = _reload_conftest()
    with (
        mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "DATABASE_URL": (
                    "postgresql+psycopg://app:app@localhost:5432/job_search_agent_test"
                ),
                "QUEUE_NAMESPACE": "job-search-agent-test",
            },
            clear=False,
        ),
    ):
        # Should not raise.
        mod._guard_test_env()


def test_guard_rejects_production_database(_preserve_env: None) -> None:
    """``DATABASE_URL`` pointing at ``job_search_agent`` → RuntimeError."""
    mod = _reload_conftest()
    with (
        mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "DATABASE_URL": ("postgresql+psycopg://app:app@localhost:5432/job_search_agent"),
                "QUEUE_NAMESPACE": "job-search-agent-test",
            },
            clear=False,
        ),
        pytest.raises(RuntimeError, match="production database"),
    ):
        mod._guard_test_env()


def test_guard_rejects_non_test_database_name(_preserve_env: None) -> None:
    """A DB name without 'test' and not in the allowlist → RuntimeError."""
    mod = _reload_conftest()
    with (
        mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "DATABASE_URL": ("postgresql+psycopg://app:app@localhost:5432/some_random_db"),
                "QUEUE_NAMESPACE": "job-search-agent-test",
            },
            clear=False,
        ),
        pytest.raises(RuntimeError, match="does not look like a test database"),
    ):
        mod._guard_test_env()


def test_guard_rejects_production_queue_namespace(_preserve_env: None) -> None:
    """``QUEUE_NAMESPACE=job-search-agent`` (prod default) → RuntimeError."""
    mod = _reload_conftest()
    with (
        mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "DATABASE_URL": (
                    "postgresql+psycopg://app:app@localhost:5432/job_search_agent_test"
                ),
                "QUEUE_NAMESPACE": "job-search-agent",
            },
            clear=False,
        ),
        pytest.raises(RuntimeError, match="QUEUE_NAMESPACE must not be"),
    ):
        mod._guard_test_env()


def test_guard_rejects_non_test_app_env(_preserve_env: None) -> None:
    """``APP_ENV`` not ``test`` → RuntimeError."""
    mod = _reload_conftest()
    with (
        mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "prod",
                "DATABASE_URL": (
                    "postgresql+psycopg://app:app@localhost:5432/job_search_agent_test"
                ),
                "QUEUE_NAMESPACE": "job-search-agent-test",
            },
            clear=False,
        ),
        pytest.raises(RuntimeError, match="APP_ENV must be 'test'"),
    ):
        mod._guard_test_env()


def test_guard_reports_all_violations_at_once(_preserve_env: None) -> None:
    """Multiple violations are all reported in a single error message."""
    mod = _reload_conftest()
    with (
        mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "prod",
                "DATABASE_URL": ("postgresql+psycopg://app:app@localhost:5432/job_search_agent"),
                "QUEUE_NAMESPACE": "job-search-agent",
            },
            clear=False,
        ),
        pytest.raises(RuntimeError) as exc_info,
    ):
        mod._guard_test_env()

    message = str(exc_info.value)
    # All three violations should appear in the single error.
    assert "APP_ENV" in message
    assert "production database" in message
    assert "QUEUE_NAMESPACE" in message


def test_extract_db_name_parses_standard_url() -> None:
    """``_extract_db_name`` extracts the db name from a standard SQLAlchemy URL."""
    mod = _reload_conftest()
    assert (
        mod._extract_db_name("postgresql+psycopg://app:app@localhost:5432/job_search_agent_test")
        == "job_search_agent_test"
    )


def test_extract_db_name_returns_empty_for_empty_url() -> None:
    """``_extract_db_name`` returns ``''`` for an empty URL."""
    mod = _reload_conftest()
    assert mod._extract_db_name("") == ""


def test_guard_passes_with_test_prefix_db_name(_preserve_env: None) -> None:
    """A DB name starting with ``test_`` is also accepted."""
    mod = _reload_conftest()
    with (
        mock.patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "DATABASE_URL": ("postgresql+psycopg://app:app@localhost:5432/test_job_search"),
                "QUEUE_NAMESPACE": "job-search-agent-test",
            },
            clear=False,
        ),
    ):
        # Should not raise — ``test_`` prefix is accepted.
        mod._guard_test_env()
