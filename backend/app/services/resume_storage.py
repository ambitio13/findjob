"""Resume file storage on local disk.

Persists uploaded resume bytes under
``{upload_dir}/{user_id}/{resume_id}/{safe_filename}`` and returns the
relative path as ``storage_uri``. Filenames are sanitized to their basename to
prevent path traversal, and all path components are reduced to a conservative
character allowlist.
"""

from __future__ import annotations

import os
import re

_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_EXTENSION_RE = re.compile(r"[^A-Za-z0-9]+")


def save_upload(
    upload_dir: str,
    user_id: str,
    resume_id: str,
    filename: str,
    content: bytes,
) -> str:
    """Persist ``content`` to disk and return the relative ``storage_uri``.

    The relative path is ``{user_id}/{resume_id}/{safe_filename}`` so callers
    can later resolve it against ``upload_dir`` or relocate the tree.
    """
    safe_filename = storage_filename(filename)
    safe_user_id = _safe_path_component(user_id, fallback="user")
    safe_resume_id = _safe_path_component(resume_id, fallback="resume")
    rel_dir = os.path.join(safe_user_id, safe_resume_id)
    abs_dir = os.path.join(upload_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(abs_dir, safe_filename)
    with open(abs_path, "wb") as fh:
        fh.write(content)
    # Use forward slashes in the stored URI for portability across OSes.
    return f"{rel_dir}/{safe_filename}"


def resolve_path(upload_dir: str, storage_uri: str) -> str:
    """Return the absolute on-disk path for a ``storage_uri``."""
    return os.path.join(upload_dir, storage_uri)


def display_filename(filename: str) -> str:
    """Return a basename-only filename suitable for DB display metadata."""
    return os.path.basename(filename.strip()) or "resume.bin"


def storage_filename(filename: str) -> str:
    """Return a sanitized filename for disk while preserving a safe extension."""
    display = display_filename(filename)
    stem, ext = os.path.splitext(display)
    safe_stem = _safe_path_component(stem, fallback="resume")
    safe_ext = _SAFE_EXTENSION_RE.sub("", ext.lstrip(".").lower())
    return f"{safe_stem}.{safe_ext}" if safe_ext else safe_stem


def _safe_path_component(value: str, *, fallback: str) -> str:
    """Return a conservative path component safe for local-disk storage."""
    cleaned = _SAFE_COMPONENT_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback
