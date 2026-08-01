"""Shared test fixtures.

Tests run against a real PostgreSQL test database (see ``.env.test`` /
``DATABASE_URL``). The model gateway uses the fake provider so no network or
API key is required. Each test function runs against a truncated database so
tests are isolated and do not leave rows behind.

Resume uploads are written to a per-session temp directory (set via
``RESUME_UPLOAD_DIR`` before the app imports its settings) so tests never touch
the real ``/data/resumes`` volume.
"""

from __future__ import annotations

import os
import tempfile

# Override the resume upload directory BEFORE the app/settings are imported so
# the Settings instance picks up the temp path. ``get_settings`` is cached on
# first call, so this must happen before ``app.main`` is imported.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("MODEL_PROVIDER", "fake")
os.environ["RESUME_UPLOAD_DIR"] = tempfile.mkdtemp(prefix="resume_test_")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    """Ensure the schema exists on the test database before any test runs.

    ``create_all`` with ``checkfirst=True`` will not *alter* an existing table
    (e.g. add a new column), so we drop first to guarantee the schema matches
    the current models. The test DB is a throwaway database.
    """
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
