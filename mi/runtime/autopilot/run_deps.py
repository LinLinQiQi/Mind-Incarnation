from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RunDeps:
    """Runtime callbacks shared by extracted flow helpers."""

    emit_prefixed: Callable[[str, str], None]
    now_ts: Callable[[], str]
    evidence_append: Callable[[dict[str, Any]], Any]
    mind_call: Callable[..., Any] | None = None
    queue_next_input: Callable[..., bool] | None = None
    read_user_answer: Callable[[str], str] | None = None
    append_auto_answer_record: Callable[..., dict[str, Any]] | None = None
    append_user_input_record: Callable[..., dict[str, Any]] | None = None
    maybe_cross_project_recall: Callable[..., Any] | None = None
