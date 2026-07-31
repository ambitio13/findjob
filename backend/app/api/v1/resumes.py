"""Resumes router placeholder."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.get("")
def list_resumes() -> dict:
    return {"items": [], "message": "resume list not yet implemented"}
