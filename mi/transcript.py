from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.runtime.transcript`.
"""

from .runtime.transcript import (
    last_agent_message_from_transcript,
    resolve_transcript_path,
    summarize_codex_events,
    summarize_hands_transcript,
    tail_transcript_lines,
)

__all__ = [
    "last_agent_message_from_transcript",
    "resolve_transcript_path",
    "summarize_codex_events",
    "summarize_hands_transcript",
    "tail_transcript_lines",
]

