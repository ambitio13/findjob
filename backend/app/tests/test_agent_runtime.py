"""Agent runtime smoke test: the deterministic JD-analysis demo workflow."""

from __future__ import annotations

import pytest

from app.agents.orchestrator import manual_jd_analysis_demo
from app.agents.runtime import ArtifactType, RunStatus


@pytest.mark.asyncio
async def test_manual_jd_analysis_demo() -> None:
    run, artifact = await manual_jd_analysis_demo(
        "Senior Python backend engineer, 5+ years, Kafka."
    )
    assert run.status == RunStatus.succeeded
    assert len(run.steps) == 2
    assert run.steps[0].name == "plan"
    assert run.steps[1].name == "generate_placeholder_analysis"
    assert artifact.artifact_type == ArtifactType.jd_analysis
    assert artifact.agent_run_id == run.id
