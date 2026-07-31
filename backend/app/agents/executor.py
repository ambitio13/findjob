"""Executor interface.

The executor runs a single planned step. It may call tools and the model
gateway. The MVP executor produces deterministic placeholder output so the
smoke workflow works without a model key.
"""

from __future__ import annotations

from typing import Any

from app.agents.planner import PlannedStep
from app.agents.runtime import AgentStepData, StepStatus
from app.models_gateway.base import ModelGateway
from app.models_gateway.fake import FakeModelGateway


class Executor:
    def __init__(self, gateway: ModelGateway | None = None) -> None:
        self._gateway = gateway or FakeModelGateway()

    async def execute(
        self, step: PlannedStep, run_id: str, step_no: int, context: dict[str, Any]
    ) -> AgentStepData:
        raise NotImplementedError


class PlaceholderExecutor(Executor):
    """Deterministic executor used by the smoke workflow."""

    async def execute(
        self, step: PlannedStep, run_id: str, step_no: int, context: dict[str, Any]
    ) -> AgentStepData:
        if step.name == "generate_placeholder_analysis":
            jd_text = str(context.get("jd_text", ""))[:200]
            result = {
                "summary": "Placeholder JD analysis (no model key configured).",
                "jd_excerpt": jd_text,
            }
        else:
            result = {"step": step.name, "ok": True}

        return AgentStepData(
            id=f"step_{run_id}_{step_no}",
            run_id=run_id,
            step_no=step_no,
            name=step.name,
            status=StepStatus.succeeded,
            result=result,
        )
