from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BatchExecutionContext:
    """Shared state for a single autopilot batch execution."""

    batch_idx: int
    batch_id: str
    batch_ts: str
    hands_transcript: Path
    batch_input: str
    hands_prompt: str
    light_injection: str
    sent_ts: str
    prompt_sha256: str
    use_resume: bool
    attempted_overlay_resume: bool
    result: Any | None = None
    summary: dict[str, Any] | None = None
    repo_observation: dict[str, Any] | None = None
    hands_last_message: str = ""
    evidence_obj: dict[str, Any] | None = None
    evidence_mind_ref: str = ""
    evidence_state: str = ""
    thought_db_context: dict[str, Any] | None = None
