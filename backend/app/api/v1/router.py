"""Versioned API router aggregating all v1 routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import agent_runs, applications, health, jobs, resumes, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(users.router)
api_router.include_router(resumes.router)
api_router.include_router(jobs.router)
api_router.include_router(applications.router)
api_router.include_router(agent_runs.router)
