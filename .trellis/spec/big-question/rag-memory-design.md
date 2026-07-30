# RAG and Memory Design

## Memory Classes

- Short-term conversation context: current session summary and latest user
  decisions.
- Long-term user profile: career goals, strengths, constraints, and preferred
  directions.
- Resume memory: parsed resume versions and evidence-backed facts.
- Job memory: discovered postings, JD snapshots, and analysis results.
- Business knowledge: interview topics, role expectations, platform rules, and
  reusable preparation material.
- Tool-result cache: recent searches, platform metadata, and expensive analysis.

## Storage Direction

- PostgreSQL for durable user, resume, job, application, artifact, and agent-run
  records.
- Redis for hot cache, locks, queue coordination, and temporary context.
- Vector storage for retrieval over resumes, JDs, interview material, and
  business knowledge after the retrieval use cases are proven.

## Retrieval Rules

- Chunk by semantic units: resume sections, JD responsibilities, requirements,
  company info, interview question groups, or policy sections.
- Keep source IDs and versions on every chunk.
- Use hybrid retrieval or reranking when keyword precision matters.
- Never let retrieved text override ownership or permission checks.

