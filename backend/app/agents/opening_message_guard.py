"""Opening-message guard and match-decision safety gate.

This module enforces deterministic backend safety rules **after** the model
returns a validated :class:`~app.schemas.boss_match_decision.MatchDecisionModelOutput`
but **before** the output is persisted or used to trigger any browser side
effect.

Two layers:

1. :func:`validate_opening_message` — checks a single opening-message string
   for length (10–500 chars), PII (phone/email/ID-card patterns), and basic
   tone. Returns ``(message_or_None, error_reason_or_None)``.

2. :func:`apply_match_safety_gate` — takes the full
   ``MatchDecisionModelOutput`` and may downgrade ``communicate`` →
   ``needs_review`` when:
   - ``score < min_score`` (low confidence; defaults to
     :data:`COMMUNICATE_MIN_SCORE`, Phase 5 may inject a per-user
     statistically calibrated value), or
   - ``missing_requirements`` is non-empty, or
   - ``opening_message`` is missing (``None``) or fails
     :func:`validate_opening_message` — a communicate decision without a
     valid message has nothing lawful to send.

   ``skip`` and ``needs_review`` decisions pass through unchanged.

These rules implement the PRD R2 requirement: "低置信度、关键字段缺失、明显不
匹配、薪资/城市/经验不符合时，不允许自动沟通" and the implement.md acceptance
criteria: "Low confidence and validation errors stop before browser side effects"
and "Opening message length/tone/PII rules are validated."
"""

from __future__ import annotations

import re

from app.schemas.boss_match_decision import MatchDecision, MatchDecisionModelOutput

#: Minimum opening-message length (characters).
OPENING_MESSAGE_MIN_LEN = 10

#: Maximum opening-message length (characters).
OPENING_MESSAGE_MAX_LEN = 500

#: Score threshold below which ``communicate`` is downgraded to ``needs_review``.
COMMUNICATE_MIN_SCORE = 0.6

# PII detection patterns. The opening message must NOT contain personal contact
# information — it goes to a recruiter who does not need it at this stage.
_PHONE_RE = re.compile(r"1[3-9]\d{9}")  # Chinese mobile: 11 digits starting 13-19
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_ID_CARD_RE = re.compile(r"\d{17}[\dXx]")  # 18-digit Chinese ID card

# Basic tone check: reject excessive repeated punctuation (e.g. "!!!???").
_EXCESSIVE_PUNCT_RE = re.compile(r"[!?！？]{4,}")


def validate_opening_message(msg: str | None) -> tuple[str | None, str | None]:
    """Validate an opening-message string.

    Returns ``(msg, None)`` when the message is valid (or ``None`` — ``None``
    is valid because it is the correct value when the decision is not
    ``communicate``).

    Returns ``(None, reason)`` when the message is invalid, where ``reason`` is
    a short human-readable string explaining why.
    """
    if msg is None:
        return None, None

    # Empty string is invalid (model returned "" instead of null).
    stripped = msg.strip()
    if not stripped:
        return None, "opening_message is empty"

    # Length check.
    if len(stripped) < OPENING_MESSAGE_MIN_LEN:
        return None, f"opening_message too short ({len(stripped)} < {OPENING_MESSAGE_MIN_LEN})"
    if len(stripped) > OPENING_MESSAGE_MAX_LEN:
        return None, f"opening_message too long ({len(stripped)} > {OPENING_MESSAGE_MAX_LEN})"

    # PII check.
    if _PHONE_RE.search(stripped):
        return None, "opening_message contains a phone number"
    if _EMAIL_RE.search(stripped):
        return None, "opening_message contains an email address"
    if _ID_CARD_RE.search(stripped):
        return None, "opening_message contains an ID-card-like pattern"

    # Tone check.
    if _EXCESSIVE_PUNCT_RE.search(stripped):
        return None, "opening_message contains excessive punctuation"

    return stripped, None


def apply_match_safety_gate(
    output: MatchDecisionModelOutput,
    *,
    min_score: float = COMMUNICATE_MIN_SCORE,
) -> MatchDecisionModelOutput:
    """Apply deterministic backend safety rules to a validated match decision.

    May downgrade ``communicate`` → ``needs_review`` when:

    - ``score < min_score`` (low confidence). ``min_score`` defaults to
      :data:`COMMUNICATE_MIN_SCORE`; callers may inject a per-user
      calibrated threshold (Phase 5) — the gate logic itself never changes.
    - ``missing_requirements`` is non-empty (key fields unverified).
    - ``opening_message`` is missing (``None``) or fails
      :func:`validate_opening_message` — communicating without a valid
      message is never allowed.

    ``skip`` and ``needs_review`` decisions pass through unchanged. When the
    decision is downgraded, ``opening_message`` is set to ``None`` so no
    message is carried into the next stage.

    Returns a **new** ``MatchDecisionModelOutput`` instance (the input is not
    mutated).
    """
    if output.decision != MatchDecision.communicate:
        return output

    # --- Low-confidence gate ---
    if output.score < min_score:
        return MatchDecisionModelOutput(
            decision=MatchDecision.needs_review,
            score=output.score,
            reasons=output.reasons,
            risks=output.risks,
            missing_requirements=output.missing_requirements,
            opening_message=None,
        )

    # --- Missing-requirements gate ---
    if output.missing_requirements:
        return MatchDecisionModelOutput(
            decision=MatchDecision.needs_review,
            score=output.score,
            reasons=output.reasons,
            risks=output.risks,
            missing_requirements=output.missing_requirements,
            opening_message=None,
        )

    # --- Opening-message validation gate ---
    # A communicate decision without a usable message (None, empty, or
    # invalid) cannot proceed — there would be nothing lawful to send.
    cleaned_msg, _error = validate_opening_message(output.opening_message)
    if cleaned_msg is None:
        return MatchDecisionModelOutput(
            decision=MatchDecision.needs_review,
            score=output.score,
            reasons=output.reasons,
            risks=output.risks,
            missing_requirements=output.missing_requirements,
            opening_message=None,
        )

    # All gates passed — return with the cleaned message (stripped whitespace).
    return MatchDecisionModelOutput(
        decision=output.decision,
        score=output.score,
        reasons=output.reasons,
        risks=output.risks,
        missing_requirements=output.missing_requirements,
        opening_message=cleaned_msg,
    )


__all__ = [
    "COMMUNICATE_MIN_SCORE",
    "OPENING_MESSAGE_MAX_LEN",
    "OPENING_MESSAGE_MIN_LEN",
    "apply_match_safety_gate",
    "validate_opening_message",
]
