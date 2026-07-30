# Backend Performance

## Long-Running Work

Job discovery, JD analysis, rewrite-snippet generation, and platform automation can be
slow. Do not run long workflows as untracked synchronous request work.

Use background workers or durable agent runs for:

- crawling or searching job platforms;
- batch JD analysis;
- resume optimization snippet generation for multiple jobs;
- interview-preparation plan generation;
- retries after external-platform failures.

## Concurrency Rules

- Limit concurrency per user and per platform.
- Use Redis locks or idempotency keys to prevent duplicate submission.
- Apply timeouts to LLM calls, browser automation, and platform requests.
- Parallelize independent reads, but keep state transitions transactional.
- Use queues for work that may outlive an HTTP request.

## Cache Rules

Cache only data with a clear invalidation rule:

- user session and permission checks;
- platform capability metadata;
- recent job-search results;
- expensive JD analysis summaries;
- tool results that are safe to reuse.

Cache keys should include the user ID, platform, query parameters, resume
version, and prompt/model version when those change the result.

## High-Concurrency Concerns

The initiation notes explicitly call out high concurrency, cache breakdown, and
capacity protection. Plan for rate limits, backpressure, queue depth monitoring,
and graceful degradation before broad platform automation.
