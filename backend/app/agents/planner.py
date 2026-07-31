"""Planner interface.

The planner turns a workflow request into an ordered list of named steps.
The MVP planner is deterministic and does not call the model; later tasks can
replace it with a model-backed planner without changing the runtime shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlannedStep:
    name: str
    description: str = ""


class Planner:
    def plan(self, workflow_type: str, context: dict) -> list[PlannedStep]:
        raise NotImplementedError


class StaticPlanner(Planner):
    """Deterministic planner for the MVP smoke workflow."""

    def plan(self, workflow_type: str, context: dict) -> list[PlannedStep]:
        if workflow_type == "manual_jd_analysis_demo":
            return [
                PlannedStep("plan", "Decide analysis steps for the JD."),
                PlannedStep(
                    "generate_placeholder_analysis",
                    "Produce a placeholder JD analysis artifact.",
                ),
            ]
        # Generic fallback: one execute step.
        return [PlannedStep("execute", f"Execute workflow {workflow_type}.")]
