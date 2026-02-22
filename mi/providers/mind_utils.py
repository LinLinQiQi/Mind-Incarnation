from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.storage import ensure_dir, filename_safe_ts, now_rfc3339


def schema_path(name: str) -> Path:
    # Schemas live under `mi/schemas` (shared across providers).
    return Path(__file__).resolve().parents[1] / "schemas" / name


def extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model output")
    try:
        return json.loads(text)
    except Exception:
        pass
    # Best-effort recovery if the model wrapped JSON with extra text.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


def new_mind_transcript_path(transcripts_dir: Path, tag: str) -> Path:
    ts = filename_safe_ts(now_rfc3339())
    return transcripts_dir / "mind" / f"{ts}_{tag}.jsonl"
