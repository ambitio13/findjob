"""Versioned API router aggregating all v1 routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agent_runs,
    applications,
    approval_actions,
    auth,
    boss_batch_loop,
    boss_communicate,
    boss_conversations,
    boss_match,
    boss_recommended_discovery,
    boss_recommended_jobs,
    health,
    jobs,
    metrics,
    resumes,
    users,
    userscript_bridge,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(resumes.router)
api_router.include_router(jobs.router)
api_router.include_router(applications.router)
api_router.include_router(approval_actions.router)
api_router.include_router(agent_runs.router)
api_router.include_router(metrics.router)
api_router.include_router(userscript_bridge.router)
api_router.include_router(boss_recommended_jobs.router)
# Register the batch-loop and discovery routers BEFORE boss_match/
# boss_communicate so the static ``/batch-loop`` and ``/discovery`` segments
# are matched before ``/{job_id}`` (see evolution-contracts.md §11 — static
# route segments must precede parameterized ones).
api_router.include_router(boss_batch_loop.router)
api_router.include_router(boss_recommended_discovery.router)
api_router.include_router(boss_match.router)
api_router.include_router(boss_communicate.router)
api_router.include_router(boss_conversations.router)
