#!/usr/bin/env python3
"""Hard-reset demo data: delete the demo user and all cascading data, then re-seed.

Usage (from the backend/ directory or with PYTHONPATH=backend):

    python scripts/reset_demo.py --i-know-this-is-demo

Safety: refuses to run when ``APP_ENV=prod`` unless ``--i-know-this-is-demo``
is passed. The reset is a destructive hard-delete — demo data has no
retention value.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.repositories import auth_user_repo  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from scripts.seed_demo import (  # noqa: E402
    DEMO_USERNAME,
    seed,
)

# Tables with a direct user_id column, in FK-safe deletion order.
_USER_TABLES = [
    "follow_up_suggestions",
    "threshold_calibrations",
    "application_outcomes",
    "application_actions",
    "generated_artifacts",
    "application_records",
    "job_postings",
    "resumes",
]

# Tables linked via agent_runs.run_id (no direct user_id column).
_RUN_LINKED_TABLES = ["tool_calls", "agent_steps"]

# Tables linked via job_postings.job_id (no direct user_id column).
_JOB_LINKED_TABLES = ["job_analyses"]

# Tables linked via resumes.resume_id (no direct user_id column).
_RESUME_LINKED_TABLES = ["resume_versions"]


def _guard(env: str, *, force: bool) -> None:
    if env == "prod" and not force:
        sys.exit(
            "ERROR: APP_ENV=prod — refusing to reset without "
            "--i-know-this-is-demo. This script performs a destructive hard-delete."
        )


def reset() -> str:
    """Delete all demo user data, then re-seed. Returns the new demo user id."""
    settings = get_settings()
    _guard(settings.app_env, force=False)

    with SessionLocal() as db:
        # Find the demo user (by username in auth_users, or by demo_user_id).
        demo_auth = auth_user_repo.get_by_username(db, DEMO_USERNAME)
        user_id = demo_auth.id if demo_auth else settings.demo_user_id

        # Hard-delete run-linked rows (no user_id — join via agent_runs).
        for table in _RUN_LINKED_TABLES:
            db.execute(
                text(
                    f"DELETE FROM {table} WHERE run_id IN "
                    "(SELECT id FROM agent_runs WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )

        # Delete agent_runs (has user_id).
        db.execute(
            text("DELETE FROM agent_runs WHERE user_id = :uid"),
            {"uid": user_id},
        )

        # Hard-delete job-linked rows (no user_id — join via job_postings).
        for table in _JOB_LINKED_TABLES:
            db.execute(
                text(
                    f"DELETE FROM {table} WHERE job_id IN "
                    "(SELECT id FROM job_postings WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )

        # Hard-delete resume-linked rows (no user_id — join via resumes).
        for table in _RESUME_LINKED_TABLES:
            db.execute(
                text(
                    f"DELETE FROM {table} WHERE resume_id IN "
                    "(SELECT id FROM resumes WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )

        # Hard-delete user-scoped rows in FK-safe order.
        for table in _USER_TABLES:
            db.execute(
                text(f"DELETE FROM {table} WHERE user_id = :uid"),
                {"uid": user_id},
            )

        # Delete the auth_users row (if registered via auth_service).
        if demo_auth is not None:
            db.execute(
                text("DELETE FROM auth_users WHERE id = :uid"),
                {"uid": user_id},
            )

        # Delete the user_profiles row.
        db.execute(
            text("DELETE FROM user_profiles WHERE id = :uid"),
            {"uid": user_id},
        )

        db.commit()

    # Re-seed fresh.
    return seed()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset demo data (hard-delete + re-seed).")
    parser.add_argument(
        "--i-know-this-is-demo",
        action="store_true",
        help="Required to run when APP_ENV=prod.",
    )
    args = parser.parse_args()

    settings = get_settings()
    _guard(settings.app_env, force=args.i_know_this_is_demo)

    user_id = reset()
    print(f"✅ Demo data reset and re-seeded. Demo user id: {user_id}")


if __name__ == "__main__":
    main()
