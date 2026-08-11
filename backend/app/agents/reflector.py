"""Reflector interfaces.

Two reflectors live here:

1. :class:`Reflector` — the MVP step reflector: validates a finished agent
   step and decides whether to proceed or fail. Passthrough today.

2. :class:`OutcomeReflector` — the Phase 5 outcome reflector. It consumes
   outcome evidence (one sample per submitted application) and surfaces
   *failure patterns* purely statistically: job directions whose reply rate
   stays below ``low_reply_rate`` across at least ``min_samples`` submissions.
   No model call is involved — the output is arithmetic the user can audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.agents.runtime import AgentStepData


class Reflector:
    def reflect(self, step: AgentStepData) -> Literal["proceed", "replan", "fail"]:
        if step.status.value == "succeeded":
            return "proceed"
        return "fail"


#: Label used when a job has no ``direction`` set (still worth aggregating).
UNKNOWN_DIRECTION = "unknown"


@dataclass(frozen=True)
class OutcomeSample:
    """One submitted application as seen by the reflector.

    ``replied`` is True when any reply-implying outcome (replied / interview
    / offer) was observed; the caller decides that mapping.
    """

    direction: str
    replied: bool


@dataclass(frozen=True)
class DirectionReplyRate:
    """Reply-rate statistic for one job direction."""

    direction: str
    submitted: int
    replied: int
    reply_rate: float


@dataclass(frozen=True)
class ReflectionResult:
    """What the outcome reflector found.

    ``low_reply_directions`` is sorted worst-first (lowest reply rate first)
    so callers can present the most urgent patterns at the top.
    """

    low_reply_directions: list[DirectionReplyRate]


class OutcomeReflector:
    """Statistical failure-pattern detector over outcome samples.

    Deterministic and dependency-free: given the same samples it always
    returns the same result, which keeps it trivially testable and auditable.
    """

    def __init__(self, *, min_samples: int = 5, low_reply_rate: float = 0.10) -> None:
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if not 0.0 <= low_reply_rate <= 1.0:
            raise ValueError("low_reply_rate must be within [0, 1]")
        self.min_samples = min_samples
        self.low_reply_rate = low_reply_rate

    def reflect(self, samples: list[OutcomeSample]) -> ReflectionResult:
        """Aggregate samples per direction and flag weak ones.

        A direction is flagged when it has at least ``min_samples`` submitted
        applications *and* its reply rate is strictly below ``low_reply_rate``.
        Directions are keyed by the job's ``direction`` label (blank values
        collapse to ``UNKNOWN_DIRECTION``).
        """
        totals: dict[str, int] = {}
        replies: dict[str, int] = {}
        for sample in samples:
            key = sample.direction.strip() or UNKNOWN_DIRECTION
            totals[key] = totals.get(key, 0) + 1
            if sample.replied:
                replies[key] = replies.get(key, 0) + 1

        flagged: list[DirectionReplyRate] = []
        for direction, submitted in totals.items():
            if submitted < self.min_samples:
                continue
            replied = replies.get(direction, 0)
            rate = replied / submitted
            if rate < self.low_reply_rate:
                flagged.append(
                    DirectionReplyRate(
                        direction=direction,
                        submitted=submitted,
                        replied=replied,
                        reply_rate=round(rate, 4),
                    )
                )
        flagged.sort(key=lambda stat: (stat.reply_rate, stat.direction))
        return ReflectionResult(low_reply_directions=flagged)


__all__ = [
    "UNKNOWN_DIRECTION",
    "DirectionReplyRate",
    "OutcomeReflector",
    "OutcomeSample",
    "ReflectionResult",
    "Reflector",
]
