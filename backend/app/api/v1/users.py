"""Users router placeholder for the MVP skeleton."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
def get_me() -> dict[str, str]:
    return {"status": "placeholder"}
