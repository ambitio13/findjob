"""Dry-run gate for the BOSS batch-loop ``auto_execute`` mode.

The gate determines whether ``mode=auto_execute`` is permitted for the batch
loop. Per ``prd.md`` R2, auto-execute is **default off** and may only be
enabled after the dry-run gate passes:

- At least **10 consecutive** dry-run entries with ``incident: false``.
- At least **2** entries recording a ``duplicate`` detection (proves the
  idempotency replay path was exercised under real conditions).

The gate reads the append-only JSONL log at
``.trellis/tasks/08-03-boss-dry-run-gate/dry-run-log.jsonl``. Each line is a
JSON object with at least:

- ``run`` — 1-based run number (monotonic).
- ``incident`` — ``true`` if anything went wrong, ``false`` otherwise.
- ``read_communication_result`` — e.g. ``succeeded``,
  ``duplicate_detected``, ``unknown``.
- ``anomalies`` — human-readable anomaly notes (``"无"`` = none).

When the gate has not passed, the API layer rejects ``mode=auto_execute``
with HTTP 422. The batch-loop service never receives ``auto_execute`` mode
until the gate passes — the approval boundary
(``evolution-contracts.md`` §1) is never weakened by this gate; it only
controls whether the batch loop may *auto-approve and auto-execute* the
prepared actions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.core.logging import get_logger

_log = get_logger("app.services.boss_dry_run_gate")

#: Minimum consecutive incident-free dry-run entries required.
REQUIRED_CONSECUTIVE_CLEAN = 10

#: Minimum number of ``duplicate_detected`` entries required.
REQUIRED_DUPLICATES = 2

#: Path to the dry-run log JSONL file (relative to the repo root).
_DRY_RUN_LOG_PATH = (
    Path(__file__).resolve()
    .parents[3]
    .joinpath(".trellis", "tasks", "08-03-boss-dry-run-gate", "dry-run-log.jsonl")
)


class DryRunEntry:
    """Parsed dry-run log entry."""

    __slots__ = ("run", "incident", "read_communication_result", "anomalies", "raw")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.run: int = raw.get("run", 0)
        self.incident: bool = bool(raw.get("incident", False))
        self.read_communication_result: str = str(
            raw.get("read_communication_result", "")
        )
        self.anomalies: str = str(raw.get("anomalies", ""))
        self.raw = raw


def _load_entries(path: Path) -> list[DryRunEntry]:
    """Load and parse all entries from the JSONL log file.

    Returns an empty list if the file does not exist (gate not passed).
    Malformed lines are skipped with a warning.
    """
    if not path.exists():
        _log.info("boss_dry_run_gate.log_not_found", path=str(path))
        return []

    entries: list[DryRunEntry] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            _log.warning(
                "boss_dry_run_gate.malformed_line",
                path=str(path),
                lineno=lineno,
            )
            continue
        if not isinstance(raw, dict):
            continue
        entries.append(DryRunEntry(raw))
    return entries


def _has_required_consecutive_clean(entries: list[DryRunEntry]) -> bool:
    """Return ``True`` if the last N entries are all incident-free.

    Scans from the end: if any of the last
    ``REQUIRED_CONSECUTIVE_CLEAN`` entries has ``incident=True``, the gate
    fails. If there are fewer than ``REQUIRED_CONSECUTIVE_CLEAN`` entries,
    the gate fails.
    """
    if len(entries) < REQUIRED_CONSECUTIVE_CLEAN:
        return False
    tail = entries[-REQUIRED_CONSECUTIVE_CLEAN:]
    return all(not e.incident for e in tail)


def _count_duplicates(entries: list[DryRunEntry]) -> int:
    """Count entries that recorded a ``duplicate_detected`` result."""
    return sum(
        1
        for e in entries
        if "duplicate" in e.read_communication_result.lower()
    )


def gate_status(
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return the dry-run gate status as a structured dict.

    Keys:
    - ``passed`` — ``True`` if all requirements are met.
    - ``consecutive_clean`` — current streak of incident-free entries at the
      tail.
    - ``required_consecutive_clean`` — the threshold.
    - ``duplicates`` — count of ``duplicate_detected`` entries.
    - ``required_duplicates`` — the threshold.
    - ``total_entries`` — total log entries parsed.
    - ``last_incident_run`` — the run number of the most recent incident, or
      ``None``.
    """
    log_path = path or _DRY_RUN_LOG_PATH
    entries = _load_entries(log_path)

    consecutive_clean = 0
    for entry in reversed(entries):
        if entry.incident:
            break
        consecutive_clean += 1

    duplicates = _count_duplicates(entries)
    last_incident_run: int | None = None
    for entry in reversed(entries):
        if entry.incident:
            last_incident_run = entry.run
            break

    passed = (
        consecutive_clean >= REQUIRED_CONSECUTIVE_CLEAN
        and duplicates >= REQUIRED_DUPLICATES
    )

    return {
        "passed": passed,
        "consecutive_clean": consecutive_clean,
        "required_consecutive_clean": REQUIRED_CONSECUTIVE_CLEAN,
        "duplicates": duplicates,
        "required_duplicates": REQUIRED_DUPLICATES,
        "total_entries": len(entries),
        "last_incident_run": last_incident_run,
    }


def assert_auto_execute_allowed(*, path: Path | None = None) -> dict[str, Any]:
    """Raise HTTP 422 if the dry-run gate has not passed.

    Returns the gate status dict on success. The API layer calls this before
    passing ``mode=auto_execute`` to the batch-loop service.
    """
    status = gate_status(path=path)
    if not status["passed"]:
        _log.info(
            "boss_dry_run_gate.auto_execute_rejected",
            consecutive_clean=status["consecutive_clean"],
            required_consecutive_clean=status["required_consecutive_clean"],
            duplicates=status["duplicates"],
            required_duplicates=status["required_duplicates"],
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "auto_execute_gate_not_passed",
                "message": (
                    "auto_execute 模式尚未开放：需要 "
                    f"{status['required_consecutive_clean']} 次连续无事故 dry-run "
                    f"（当前 {status['consecutive_clean']}）且至少 "
                    f"{status['required_duplicates']} 次 duplicate 检测"
                    f"（当前 {status['duplicates']}）"
                ),
                "gate_status": status,
            },
        )
    return status


__all__ = [
    "REQUIRED_CONSECUTIVE_CLEAN",
    "REQUIRED_DUPLICATES",
    "assert_auto_execute_allowed",
    "gate_status",
]
