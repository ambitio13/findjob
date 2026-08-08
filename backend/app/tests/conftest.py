"""Shared test fixtures.

Tests run against a real PostgreSQL test database (see ``.env.test`` /
``DATABASE_URL``). The model gateway uses the fake provider so no network or
API key is required. Each test function runs against a truncated database so
tests are isolated and do not leave rows behind.

Resume uploads are written to a per-session temp directory (set via
``RESUME_UPLOAD_DIR`` before the app imports its settings) so tests never touch
the real ``/data/resumes`` volume.

Test isolation safety guard
---------------------------
Before any schema-destructive operation (``drop_all``), :func:`_guard_test_env`
verifies that the runtime configuration is explicitly test-safe:

- ``APP_ENV`` must be ``test``.
- ``DATABASE_URL`` must point at a test database (name containing ``_test`` or
  ``test_``, or in the explicit allowlist). The production database
  ``job_search_agent`` is always rejected.
- ``QUEUE_NAMESPACE`` must not be the production default
  ``job-search-agent`` — otherwise a running Compose worker could consume test
  jobs and pollute business state (the exact regression that motivated this
  guard).

The guard raises ``RuntimeError`` on violation so the test session fails fast
instead of silently dropping a business database or enqueueing into a live
worker queue.
"""

from __future__ import annotations

import os
import re
import tempfile

# Force the test environment BEFORE the app/settings are imported so the
# Settings instance picks up the correct values. ``get_settings`` is cached on
# first call, so this must happen before ``app.main`` is imported.
#
# We use ``os.environ[...] = ...`` (not ``setdefault``) because the Docker
# container may already have ``MODEL_PROVIDER=auto`` and ``APP_ENV=prod`` in
# its environment. ``setdefault`` would leave those in place, causing tests to
# call the real DeepSeek API instead of the fake gateway.
os.environ["APP_ENV"] = "test"
os.environ["MODEL_PROVIDER"] = "fake"
os.environ["RESUME_UPLOAD_DIR"] = tempfile.mkdtemp(prefix="resume_test_")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app

#: Database names that are explicitly allowed for test use even if they don't
#: match the ``_test`` / ``test_`` heuristic (e.g. CI databases with custom
#: naming). The production database ``job_search_agent`` is NEVER allowed.
_TEST_DB_ALLOWLIST: frozenset[str] = frozenset()

#: The production database name — always rejected as a test target.
_PROD_DB_NAME = "job_search_agent"

#: The production queue namespace — always rejected as a test target.
_PROD_QUEUE_NAMESPACE = "job-search-agent"


def _guard_test_env() -> None:
    """Fail fast if the runtime config is not explicitly test-safe.

    Called before :func:`_create_schema` drops tables, so a misconfigured
    ``DATABASE_URL`` cannot destroy a business database. Also guards
    ``QUEUE_NAMESPACE`` so test jobs are not consumed by a live Compose worker
    sharing the production namespace.
    """
    errors: list[str] = []

    app_env = os.environ.get("APP_ENV", "")
    if app_env != "test":
        errors.append(
            f"APP_ENV must be 'test' for the test session (got {app_env!r}). "
            "Set APP_ENV=test before running pytest."
        )

    database_url = os.environ.get("DATABASE_URL", "")
    db_name = _extract_db_name(database_url)
    if not db_name:
        errors.append("DATABASE_URL must be set and contain a database name for tests.")
    elif db_name == _PROD_DB_NAME:
        errors.append(
            f"DATABASE_URL points at the production database "
            f"{_PROD_DB_NAME!r} — refusing to run tests against it."
        )
    elif "test" not in db_name.lower() and db_name not in _TEST_DB_ALLOWLIST:
        errors.append(
            f"DATABASE_URL database name {db_name!r} does not look like a "
            "test database. The name must contain 'test' (e.g. "
            "'job_search_agent_test') or be added to the test allowlist."
        )

    queue_ns = os.environ.get("QUEUE_NAMESPACE", _PROD_QUEUE_NAMESPACE)
    if queue_ns == _PROD_QUEUE_NAMESPACE:
        errors.append(
            f"QUEUE_NAMESPACE must not be the production default "
            f"{_PROD_QUEUE_NAMESPACE!r} — a running Compose worker could "
            "consume test jobs. Set QUEUE_NAMESPACE to a test value (e.g. "
            "'job-search-agent-test')."
        )

    if errors:
        raise RuntimeError("Test environment safety guard failed:\n  - " + "\n  - ".join(errors))


def _extract_db_name(database_url: str) -> str:
    """Extract the database name from a SQLAlchemy ``DATABASE_URL``.

    Returns an empty string if the URL is empty or has no path component.
    """
    if not database_url:
        return ""
    # ``postgresql+psycopg://user:pass@host:5432/db_name``
    match = re.search(r"/([^/?]+)(?:\?|$)", database_url)
    return match.group(1) if match else ""


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    """Ensure the schema exists on the test database before any test runs.

    ``create_all`` with ``checkfirst=True`` will not *alter* an existing table
    (e.g. add a new column), so we drop first to guarantee the schema matches
    the current models. The test DB is a throwaway database.

    The safety guard runs *before* ``drop_all`` so a misconfigured
    ``DATABASE_URL`` or ``QUEUE_NAMESPACE`` fails the session fast instead of
    destroying a business database or polluting a live worker queue.
    """
    _guard_test_env()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient with a clean database for each test."""
    # Truncate all domain tables (respecting FK order by truncating cascade).
    with SessionLocal() as db:
        db.execute(
            text(
                "TRUNCATE TABLE user_profiles, resumes, resume_versions, "
                "job_postings, job_analyses, generated_artifacts, "
                "application_records, application_actions, agent_runs, "
                "agent_steps, tool_calls "
                "RESTART IDENTITY CASCADE"
            )
        )
        db.commit()
    return TestClient(app)
