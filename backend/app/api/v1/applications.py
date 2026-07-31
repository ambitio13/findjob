"""Applications router placeholder."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("")
def list_applications() -> dict:
    return {"items": [], "message": "application list not yet implemented"}
