"""Custom SQLAlchemy column types.

``EncryptedText`` is a transparent encryption wrapper around ``Text`` used
for user-supplied long content that must never be persisted in plaintext
(currently ``job_postings.jd_raw``). Every ORM write encrypts and every ORM
read decrypts; raw-SQL access sees only the ``enc1$`` envelope. See
``app.core.content_crypto`` for the envelope semantics and legacy plaintext
pass-through.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.content_crypto import decrypt_text, encrypt_text


class EncryptedText(TypeDecorator):
    """``Text`` column that encrypts at rest and decrypts on load."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return encrypt_text(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return decrypt_text(value)


__all__ = ["EncryptedText"]
