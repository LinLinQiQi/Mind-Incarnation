from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .storage import atomic_write_text, ensure_dir


def write_transcript_header(path: Path, meta: dict[str, Any]) -> None:
    """Start (or overwrite) a transcript with a header record."""
    ensure_dir(path.parent)
    atomic_write_text(path, json.dumps({"type": "mi.transcript.header", **meta}) + "\n")


def append_transcript_line(path: Path, record: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

