"""JD paste parsing service — workflow orchestration.

This module owns the JD paste parsing workflow. It mirrors the resume-fact
extraction orchestration pattern (``resume_fact_service.py``) but is scoped to
a paste action that creates no ``JobPosting``:

- :func:`parse_jd`: the deterministic orchestration transaction. It creates an
  ``AgentRun`` (``workflow_type="jd_paste_parsing"``), drives the fixed steps
  (``load_context`` → ``build_prompt_context`` → ``call_model`` →
  ``validate_model_output`` → ``persist_outputs`` → ``complete_run``), persists
  ``AgentStep`` rows, and translates model/provider/schema failures into a
  recoverable failed run.

Ownership / failure contract (design.md):

- The request is scoped to ``current_user`` (the API layer resolves the user
  before this service is called).
- Blank ``raw_jd`` is rejected by the API schema (422) before this service is
  entered; the service assumes non-blank input.
- Model/provider/schema failure → failed ``AgentRun`` + ``AgentStep`` persisted
  (sanitized), ``JdParseOutcome(status="failed")`` with empty typed fields. The
  API layer returns HTTP 200 with the failed run so the user can fall back to
  manual entry.

Step results are sanitized (counts, provider/model/prompt_version, latency,
validation status) — never raw JD text or parsed field values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.agents.jd_paste_executor import (
    JdPasteExecution,
    JdPasteExecutor,
    JdPasteValidationError,
    _usage_to_dict,
)
from app.agents.prompts.jd_paste import PROMPT_VERSION
from app.core.logging import get_logger
from app.db.models.models import UserProfile
from app.db.repositories import agent_run_repo
from app.db.repositories.agent_run_repo import AgentRun
from app.models_gateway.base import ModelGateway
from app.schemas.jd_parse import JdParseExtraction
from app.schemas.jd_paste_facts import JdPasteFactsModelOutput

_log = get_logger("app.services.jd_parse_service")

#: The fixed workflow type stored on ``AgentRun.workflow_type``.
WORKFLOW_TYPE = "jd_paste_parsing"

#: Fixed step names produced by the workflow. The order matches the step_no
#: persisted on each ``AgentStep``. Splitting the coarse model call into
#: ``build_prompt_context`` / ``call_model`` / ``validate_model_output`` lets
#: the audit UI pinpoint whether a failure happened during prompt construction,
#: the model call, or output validation.
_STEP_LOAD_CONTEXT = "load_context"
_STEP_BUILD_PROMPT_CONTEXT = "build_prompt_context"
_STEP_CALL_MODEL = "call_model"
_STEP_VALIDATE_MODEL_OUTPUT = "validate_model_output"
_STEP_PERSIST_OUTPUTS = "persist_outputs"
_STEP_COMPLETE_RUN = "complete_run"


@dataclass
class JdParseOutcome:
    """Result carrier returned to the API layer.

    ``fields`` is the validated parsed draft on success, or an empty
    :class:`JdPasteFactsModelOutput` on failure so the frontend contract stays
    stable. ``extraction`` carries the parse provenance (run_id / prompt_version
    / provider / model / parsed_at) the frontend persists into
    ``jd_normalized._extraction``. ``raw_jd`` is echoed back for the form to
    re-submit on save.
    """

    status: Literal["succeeded", "failed"]
    run: AgentRun
    fields: JdPasteFactsModelOutput
    extraction: JdParseExtraction
    raw_jd: str


async def parse_jd(
    db: Session,
    current_user: UserProfile,
    raw_jd: str,
    platform_hint: str | None,
    gateway: ModelGateway,
) -> JdParseOutcome:
    """Drive the JD paste parsing workflow end to end.

    Fixed steps:

    1. ``load_context`` — record sanitized input metadata (raw_jd_len,
       platform_hint).
    2. ``build_prompt_context`` — assemble the chat messages + truncation.
    3. ``call_model`` — send the chat request via the gateway.
    4. ``validate_model_output`` — parse + schema-validate the response. On
       :class:`JdPasteValidationError` a FAILED ``AgentRun`` + ``AgentStep``
       are persisted (sanitized) and a failed outcome is returned.
    5. ``persist_outputs`` — record field/uncertain counts (the parsed fields
       live in the API response, not in step metadata).
    6. ``complete_run`` — finalize run status and metadata.

    The API layer assumes blank ``raw_jd`` was already rejected (422). This
    service does not create a skipped-status branch for invalid input.
    """
    started_at = datetime.now(UTC)
    run = agent_run_repo.create_run(
        db,
        user_id=current_user.id,
        workflow_type=WORKFLOW_TYPE,
        status="running",
        started_at=started_at,
    )

    executor = JdPasteExecutor(gateway)

    def _fail_run(
        step_no: int, step_name: str, *, error: str, result: dict[str, Any]
    ) -> JdParseOutcome:
        """Persist a failed step + failed run, commit (sanitized), and return.

        Mirrors ``resume_fact_service._fail_run``. The failing step records a
        sanitized result + error, the run flips to ``failed`` with sanitized
        metadata, and the outcome carries empty typed fields so the frontend
        can fall back to manual entry.
        """
        agent_run_repo.add_step(
            db,
            run_id=run.id,
            step_no=step_no,
            name=step_name,
            status="failed",
            result=result,
            error=error,
        )
        agent_run_repo.update_status(
            db,
            run,
            status="failed",
            finished_at=datetime.now(UTC),
            error=error,
            result={
                "user_id": current_user.id,
                "failure": "parse_failed",
                **result,
            },
        )
        db.commit()
        return JdParseOutcome(
            status="failed",
            run=run,
            fields=JdPasteFactsModelOutput(),
            extraction=_build_extraction(run, status="failed", provider=None, model=None),
            raw_jd=raw_jd,
        )

    # Step 1 — load_context.
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=1,
        name=_STEP_LOAD_CONTEXT,
        status="succeeded",
        result={
            "raw_jd_len": len(raw_jd),
            "platform_hint": platform_hint,
        },
    )

    # Step 2 — build_prompt_context.
    prompt = executor.build_prompt(raw_jd, platform_hint)
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=2,
        name=_STEP_BUILD_PROMPT_CONTEXT,
        status="succeeded",
        result={
            "prompt_version": PROMPT_VERSION,
            "message_count": len(prompt.messages),
            "truncation": prompt.truncation,
        },
    )

    # Step 3 — call_model. A gateway/provider error is treated as a failed run.
    try:
        response = await executor.call_model(prompt.messages)
    except Exception as exc:
        _log.warning(
            "jd_paste.model_call_failed",
            run_id=run.id,
            provider=gateway.provider_name,
            error=str(exc),
        )
        return _fail_run(
            3,
            _STEP_CALL_MODEL,
            error="model call failed",
            result={
                "provider": gateway.provider_name,
                "error_type": type(exc).__name__,
            },
        )

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=3,
        name=_STEP_CALL_MODEL,
        status="succeeded",
        result={
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
            "latency_ms": response.latency_ms,
        },
    )

    # Step 4 — validate_model_output. On validation failure persist a failed
    # step + failed run (sanitized, no raw JD content).
    try:
        output = executor.validate(response)
    except JdPasteValidationError as exc:
        _log.warning(
            "jd_paste.model_invalid",
            run_id=run.id,
            kind=exc.kind,
            request_id=exc.request_id,
            provider=response.provider,
        )
        return _fail_run(
            4,
            _STEP_VALIDATE_MODEL_OUTPUT,
            error="model returned invalid JD parse output",
            result={"kind": exc.kind, "request_id": exc.request_id},
        )

    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=4,
        name=_STEP_VALIDATE_MODEL_OUTPUT,
        status="succeeded",
        result={
            "kind": "schema_valid",
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
        },
    )

    execution = JdPasteExecution(
        output=output,
        truncation=prompt.truncation,
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        latency_ms=response.latency_ms,
        usage=_usage_to_dict(response.usage),
    )

    # Step 5 — persist_outputs. No JobPosting to write (parse-then-create);
    # the parsed draft lives in the API response. Step records counts only.
    field_count = _count_non_empty_fields(output)
    uncertain_count = len(output.uncertain_fields)
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=5,
        name=_STEP_PERSIST_OUTPUTS,
        status="succeeded",
        result={
            "field_count": field_count,
            "uncertain_count": uncertain_count,
        },
    )

    # Step 6 — complete_run.
    agent_run_repo.add_step(
        db,
        run_id=run.id,
        step_no=6,
        name=_STEP_COMPLETE_RUN,
        status="succeeded",
        result={"status": "succeeded"},
    )

    agent_run_repo.update_status(
        db,
        run,
        status="succeeded",
        finished_at=datetime.now(UTC),
        result={
            "user_id": current_user.id,
            "field_count": field_count,
            "uncertain_count": uncertain_count,
            "provider": execution.provider,
            "model": execution.model,
            "model_request_id": execution.request_id,
            "prompt_version": PROMPT_VERSION,
            "source_context": {
                "truncation": execution.truncation,
            },
        },
    )

    db.commit()
    db.refresh(run)
    return JdParseOutcome(
        status="succeeded",
        run=run,
        fields=output,
        extraction=_build_extraction(
            run, status="succeeded", provider=execution.provider, model=execution.model
        ),
        raw_jd=raw_jd,
    )


def _build_extraction(
    run: AgentRun,
    *,
    status: Literal["succeeded", "failed"],
    provider: str | None,
    model: str | None,
) -> JdParseExtraction:
    """Assemble the parse-provenance block the frontend persists in
    ``jd_normalized._extraction`` (design.md §"jd_normalized shape").

    ``parsed_at`` is taken from the run's ``started_at`` so the timestamp is
    the same one stored on the durable AgentRun row.
    """
    parsed_at = run.started_at.isoformat() if run.started_at else datetime.now(UTC).isoformat()
    return JdParseExtraction(
        status=status,
        run_id=run.id,
        parsed_at=parsed_at,
        prompt_version=PROMPT_VERSION,
        provider=provider,
        model=model,
    )


def _count_non_empty_fields(output: JdPasteFactsModelOutput) -> int:
    """Count how many top-level fields have a non-empty value.

    Used for sanitized step metadata (``field_count``) so the run audit shows
    parse richness without exposing the parsed field values themselves.
    """
    count = 0
    for value in (
        output.title,
        output.company,
        output.platform,
        output.location,
        output.salary_range,
        output.direction,
    ):
        if value:
            count += 1
    for lst in (
        output.responsibilities,
        output.hard_requirements,
        output.nice_to_have_requirements,
        output.benefits_or_risk_clues,
    ):
        if lst:
            count += 1
    return count
