"""encrypt legacy plaintext job_postings.jd_raw

Revision ID: 0010_encrypt_jd_raw
Revises: 0009_followup_calibration
Create Date: 2026-08-11

Phase-review remediation (privacy invariant): raw JD text must not be stored
in plaintext. New writes already go through the ``EncryptedText`` column
type; this data migration cleans up rows written before that change by
wrapping every value that lacks the ``enc1$`` envelope.

The envelope format and key derivation live in ``app.core.content_crypto``
so the migration and the runtime use exactly the same crypto. Downgrade
decrypts back to plaintext (the previous, weaker state).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.content_crypto import ENCRYPTED_PREFIX, decrypt_text, encrypt_text

# revision identifiers, used by Alembic.
revision: str = "0010_encrypt_jd_raw"
down_revision: str | None = "0009_followup_calibration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Rows are small JD texts; chunking keeps memory bounded on large tables.
_BATCH_SIZE = 500


def _fetch_pending(conn, like: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        sa.text("SELECT id, jd_raw FROM job_postings WHERE jd_raw NOT LIKE :p"),
        {"p": like},
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def upgrade() -> None:
    conn = op.get_bind()
    pending = [(rid, v) for rid, v in _fetch_pending(conn, f"{ENCRYPTED_PREFIX}%") if v]
    for start in range(0, len(pending), _BATCH_SIZE):
        for rid, plain in pending[start : start + _BATCH_SIZE]:
            conn.execute(
                sa.text("UPDATE job_postings SET jd_raw = :v WHERE id = :id"),
                {"v": encrypt_text(plain), "id": rid},
            )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, jd_raw FROM job_postings WHERE jd_raw LIKE :p"),
        {"p": f"{ENCRYPTED_PREFIX}%"},
    ).fetchall()
    for start in range(0, len(rows), _BATCH_SIZE):
        for row in rows[start : start + _BATCH_SIZE]:
            conn.execute(
                sa.text("UPDATE job_postings SET jd_raw = :v WHERE id = :id"),
                {"v": decrypt_text(row[1]), "id": row[0]},
            )
