# Design

## Workflow Shape

```text
POST /applications/{id}/artifacts/{artifact_type}/generate
  -> verify application/job/resume ownership
  -> compute source snapshot
  -> create queued AgentRun
  -> enqueue artifact generation payload
  -> return 202 with run id

worker
  -> reload application/job/resume/profile
  -> re-check source snapshot
  -> build prompt from editable template
  -> call model
  -> validate output
  -> persist GeneratedArtifact
  -> append application timeline event
  -> mark run succeeded
```

## Artifact Output Contracts

Keep output structured per artifact type.

### HR Opening Message

```json
{
  "hook": "20字以内亮点",
  "message": "完整可复制话术",
  "evidence": ["source-backed bullet"],
  "risk_note": "optional"
}
```

### Resume Rewrite Snippet

```json
{
  "project_snippets": [],
  "skill_snippets": [],
  "experience_snippets": [],
  "do_not_claim": []
}
```

### Skill Gap Plan

```json
{
  "critical_gaps": [],
  "quick_wins": [],
  "study_plan": [],
  "interview_risk": []
}
```

### Interview Prep

```json
{
  "likely_questions": [],
  "answer_points": [],
  "portfolio_talking_points": [],
  "questions_to_ask_interviewer": []
}
```

## Freshness

Persist `source_hash` in artifact `source_ids`. UI later compares current
source hash to artifact source hash.

## Failure Persistence

Failures should update:

- AgentRun status/result;
- AgentStep error/result;
- ApplicationRecord latest failure envelope;
- Application timeline event.

Do not create a `GeneratedArtifact` row for invalid output.

