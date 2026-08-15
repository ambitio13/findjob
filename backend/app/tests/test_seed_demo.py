"""Tests for the demo seed/reset scripts.

Covers idempotency (running seed twice produces the same row counts) and the
reset cycle (reset clears all demo data then re-seeds to the same state).
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models.models import (
    ApplicationAction,
    ApplicationOutcome,
    ApplicationRecord,
    GeneratedArtifact,
    JobAnalysis,
    JobPosting,
    Resume,
    ResumeVersion,
)
from app.db.session import SessionLocal
from scripts.reset_demo import reset
from scripts.seed_demo import DEMO_USERNAME, seed


def _count_user_rows(user_id: str) -> dict[str, int]:
    """Count all user-scoped rows for a given user_id."""
    with SessionLocal() as db:
        return {
            "jobs": db.execute(
                select(func.count()).select_from(JobPosting).where(JobPosting.user_id == user_id)
            ).scalar_one(),
            "resumes": db.execute(
                select(func.count()).select_from(Resume).where(Resume.user_id == user_id)
            ).scalar_one(),
            "resume_versions": db.execute(
                select(func.count(ResumeVersion.id)
                ).where(
                    ResumeVersion.resume_id.in_(
                        select(Resume.id).where(Resume.user_id == user_id)
                    )
                )
            ).scalar_one(),
            "applications": db.execute(
                select(func.count()).select_from(ApplicationRecord).where(
                    ApplicationRecord.user_id == user_id
                )
            ).scalar_one(),
            "actions": db.execute(
                select(func.count()).select_from(ApplicationAction).where(
                    ApplicationAction.user_id == user_id
                )
            ).scalar_one(),
            "outcomes": db.execute(
                select(func.count()).select_from(ApplicationOutcome).where(
                    ApplicationOutcome.user_id == user_id
                )
            ).scalar_one(),
            "match_artifacts": db.execute(
                select(func.count()).select_from(GeneratedArtifact).where(
                    GeneratedArtifact.user_id == user_id,
                    GeneratedArtifact.artifact_type == "boss_match_decision",
                )
            ).scalar_one(),
            "job_analyses": db.execute(
                select(func.count()).select_from(JobAnalysis).where(
                    JobAnalysis.job_id.in_(
                        select(JobPosting.id).where(JobPosting.user_id == user_id)
                    )
                )
            ).scalar_one(),
        }


class TestSeedDemo:
    """Seed script: creates demo user + preset data, idempotent."""

    def test_seed_creates_expected_data(self, client):
        """First seed creates demo user with 3 jobs, 1 resume, match decisions, applications."""
        user_id = seed()
        assert user_id is not None and len(user_id) > 0

        counts = _count_user_rows(user_id)
        # 3 preset jobs
        assert counts["jobs"] == 3
        # 1 resume with 1 version
        assert counts["resumes"] == 1
        assert counts["resume_versions"] == 1
        # 3 match decision artifacts (one per job)
        assert counts["match_artifacts"] == 3
        # 3 job analyses (one per job)
        assert counts["job_analyses"] == 3
        # 2 applications (only matched jobs get applications — 2 of 3 are "communicate")
        assert counts["applications"] == 2
        # 2 actions (one per application)
        assert counts["actions"] == 2
        # 1 outcome (only the first matched application has an outcome)
        assert counts["outcomes"] == 1

    def test_seed_is_idempotent(self, client):
        """Running seed twice produces the same row counts — no duplicates."""
        user_id = seed()
        first_counts = _count_user_rows(user_id)

        seed()
        second_counts = _count_user_rows(user_id)

        assert first_counts == second_counts, (
            "Seed is not idempotent — second run produced different counts"
        )

    def test_seed_demo_username_constant(self):
        """The demo username is the documented constant."""
        assert DEMO_USERNAME == "demo"


class TestResetDemo:
    """Reset script: hard-deletes all demo data then re-seeds."""

    def test_reset_clears_then_reseeds(self, client):
        """After reset, counts match a fresh seed."""
        # Seed first
        user_id = seed()
        pre_counts = _count_user_rows(user_id)
        assert pre_counts["jobs"] > 0, "Seed should have created jobs before reset"

        # Reset
        new_user_id = reset()
        post_counts = _count_user_rows(new_user_id)

        # After reset+reseed, counts should match the original seed
        assert post_counts == pre_counts, (
            "Reset+reseed did not restore the same data state"
        )

    def test_reset_is_idempotent(self, client):
        """Running reset twice produces the same result as once."""
        seed()
        reset()
        user_id_1 = reset()
        counts_1 = _count_user_rows(user_id_1)

        user_id_2 = reset()
        counts_2 = _count_user_rows(user_id_2)

        assert counts_1 == counts_2, (
            "Reset is not idempotent — second run produced different counts"
        )
