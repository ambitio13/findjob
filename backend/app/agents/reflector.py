"""Reflector interface.

The reflector validates step results and decides whether to re-plan or
proceed. The MVP reflector is a passthrough that marks every successful step
as accepted.
"""

from __future__ import annotations

from typing import Literal

from app.agents.runtime import AgentStepData


class Reflector:
    def reflect(self, step: AgentStepData) -> Literal["proceed", "replan", "fail"]:
        if step.status.value == "succeeded":
            return "proceed"
        return "fail"
