from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...core.storage import filename_safe_ts

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


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_batch_execution_context(
    *,
    batch_idx: int,
    transcripts_dir: Path,
    next_input: str,
    thread_id: str | None,
    hands_resume: Any | None,
    resumed_from_overlay: bool,
    now_ts: Callable[[], str],
    build_light_injection_for_ts: Callable[[str], str],
) -> BatchExecutionContext:
    """Build one batch execution context with deterministic prompt metadata."""

    batch_id = f"b{batch_idx}"
    batch_ts = filename_safe_ts(now_ts())
    light = build_light_injection_for_ts(now_ts())
    batch_input = str(next_input or "").strip()
    hands_prompt = light + "\n" + batch_input + "\n"
    sent_ts = now_ts()
    prompt_sha256 = _sha256_text(hands_prompt)
    use_resume = thread_id is not None and hands_resume is not None and thread_id != "unknown"
    attempted_overlay_resume = bool(use_resume and resumed_from_overlay and batch_idx == 0)

    return BatchExecutionContext(
        batch_idx=batch_idx,
        batch_id=batch_id,
        batch_ts=batch_ts,
        hands_transcript=transcripts_dir / "hands" / f"{batch_ts}_b{batch_idx}.jsonl",
        batch_input=batch_input,
        hands_prompt=hands_prompt,
        light_injection=light,
        sent_ts=sent_ts,
        prompt_sha256=prompt_sha256,
        use_resume=use_resume,
        attempted_overlay_resume=attempted_overlay_resume,
    )
