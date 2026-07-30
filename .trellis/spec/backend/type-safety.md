# Backend Type Safety

## Python Typing

- Use type annotations for all public service, repository, and tool-adapter
  functions.
- Prefer explicit domain types and Pydantic models over untyped dictionaries.
- Avoid passing raw LLM or platform payloads across layers. Normalize them at
  the adapter boundary.
- Use enums or constrained literals for state machines.

## Pydantic Rules

Pydantic models should define all API input and output shapes. Separate create,
update, database, and response schemas when fields differ.

```python
from enum import StrEnum
from pydantic import BaseModel, Field


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    ANALYZED = "analyzed"
    APPROVED = "approved"
    SUBMITTED = "submitted"


class JobAnalysisCreate(BaseModel):
    job_id: str
    resume_version_id: str
    risk_score: int = Field(ge=0, le=100)
    summary: str
```

## Domain Contract Rules

- Distinguish original external data from normalized internal data.
- Preserve resume versions; never silently overwrite a resume that was used to
  generate an application artifact.
- Store AI-generated output with source IDs and generation metadata.
- Use typed tool-call input and output schemas for every agent tool.

