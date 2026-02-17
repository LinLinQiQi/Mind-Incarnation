from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.core.storage`.
"""

from .core.storage import (
    append_jsonl,
    atomic_write_text,
    ensure_dir,
    iter_jsonl,
    now_rfc3339,
    read_json,
    write_json,
)

__all__ = [
    "append_jsonl",
    "atomic_write_text",
    "ensure_dir",
    "iter_jsonl",
    "now_rfc3339",
    "read_json",
    "write_json",
]

