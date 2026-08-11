"""Golden-set prompt regression (Phase 6 evaluation system).

Guards against silent quality regressions in the deterministic safety layer:
the golden set (``tests/golden/``) annotates raw model outputs and the gated
decision they must produce, plus JD red-flag annotation vocabulary. Running
these cases in CI means any change to the guard/gate logic, the threshold
semantics, or the red-flag schema that alters behaviour fails loudly — the
"改 prompt 不引入质量回退" contract from the evolution plan.

The cases run fully offline against pure functions (no model, no network),
which the plan explicitly sanctions ("离线用 Fake"). Model-quality scoring
against the golden set plugs into the same fixtures once a real provider is
wired into the eval harness; the online track is the Phase 1 funnel +
calibration metrics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest

from app.agents.opening_message_guard import COMMUNICATE_MIN_SCORE, apply_match_safety_gate
from app.schemas.boss_match_decision import MatchDecision, MatchDecisionModelOutput
from app.schemas.jd_analysis import JdRedFlag, RedFlagType

_GOLDEN_DIR = Path(__file__).parent / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    return json.loads((_GOLDEN_DIR / name).read_text(encoding="utf-8"))


def _gate_cases() -> list[dict[str, Any]]:
    return _load_golden("prompt_regression_cases.json")["cases"]


def _red_flag_cases() -> list[dict[str, Any]]:
    return _load_golden("jd_red_flag_cases.json")["cases"]


@pytest.mark.parametrize(
    "case",
    _gate_cases(),
    ids=[c["id"] for c in _gate_cases()],
)
def test_safety_gate_matches_golden_expectation(case: dict[str, Any]) -> None:
    """Each annotated raw model output must gate to its expected decision."""
    model_output = MatchDecisionModelOutput.model_validate(case["model_output"])
    min_score = case.get("min_score", COMMUNICATE_MIN_SCORE)

    gated = apply_match_safety_gate(model_output, min_score=min_score)

    expected = case["expected"]
    assert gated.decision == MatchDecision(expected["decision"]), (
        f"case {case['id']}: expected {expected['decision']}, got {gated.decision.value}"
    )
    has_message = gated.opening_message is not None
    assert has_message == expected["opening_message_present"], (
        f"case {case['id']}: opening_message presence mismatch"
    )
    if has_message:
        # The gated message content is part of the contract too: explicit
        # annotations win, otherwise a passing message must be the stripped
        # model output (no silent rewriting).
        expected_text = expected.get("opening_message")
        if expected_text is None:
            expected_text = case["model_output"]["opening_message"].strip()
        assert gated.opening_message == expected_text, (
            f"case {case['id']}: opening_message content mismatch"
        )


@pytest.mark.parametrize(
    "case",
    _gate_cases(),
    ids=[c["id"] for c in _gate_cases()],
)
def test_gate_never_upgrades_to_communicate(case: dict[str, Any]) -> None:
    """Safety invariant: a non-communicate input never becomes communicate."""
    model_output = MatchDecisionModelOutput.model_validate(case["model_output"])
    gated = apply_match_safety_gate(
        model_output, min_score=case.get("min_score", COMMUNICATE_MIN_SCORE)
    )
    if model_output.decision is not MatchDecision.communicate:
        assert gated.decision is not MatchDecision.communicate


def test_red_flag_annotation_vocabulary_matches_schema() -> None:
    """Golden red-flag annotations stay consistent with the JdRedFlag schema.

    Every annotated ``flag_type`` must be a valid ``RedFlagType`` member and
    every expected flag must construct a valid ``JdRedFlag`` — so the golden
    set cannot drift from the schema it is meant to evaluate.
    """
    allowed_flag_types = set(get_args(RedFlagType))
    for case in _red_flag_cases():
        assert case["jd_excerpt"].strip(), f"case {case['id']}: empty JD excerpt"
        for flag in case["expected_red_flags"]:
            assert flag["flag_type"] in allowed_flag_types, (
                f"case {case['id']}: unknown flag_type {flag['flag_type']!r}"
            )
            red_flag = JdRedFlag(
                flag_type=flag["flag_type"],
                title=f"golden:{case['id']}",
                detail=flag["flag_type"],
                severity=flag["severity"],
                evidence_quote=case["jd_excerpt"],
            )
            assert red_flag.flag_type == flag["flag_type"]


def test_golden_sets_are_non_empty() -> None:
    """Guard against accidental emptying of the evaluation corpus."""
    assert len(_gate_cases()) >= 10, "match-gate golden set shrank unexpectedly"
    assert len(_red_flag_cases()) >= 4, "red-flag golden set shrank unexpectedly"
