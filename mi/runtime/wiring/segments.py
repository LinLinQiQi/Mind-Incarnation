from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..autopilot.segment_state import clear_segment_state, load_segment_state, new_segment_state, persist_segment_state


@dataclass(frozen=True)
class SegmentStateIO:
    """Best-effort IO + bootstrap for segment_state.json (checkpoint infrastructure).

    This is wiring-only: the segment buffer is an internal mechanism for checkpoint-driven
    mining/materialization. It does not impose a step protocol on the Hands agent.
    """

    path: Path
    task: str
    now_ts: Callable[[], str]
    truncate: Callable[[str, int], str]
    read_json_best_effort: Callable[..., Any]
    write_json_atomic: Callable[[Path, Any], None]
    state_warnings: list[dict[str, Any]]
    segment_max_records: int = 40

    def new_state(self, *, reason: str, thread_hint: str) -> dict[str, Any]:
        return new_segment_state(
            reason=str(reason or "").strip(),
            thread_hint=str(thread_hint or "").strip(),
            task=str(self.task or ""),
            now_ts=self.now_ts,
            truncate=self.truncate,
            id_factory=lambda: f"seg_{time.time_ns()}_{secrets.token_hex(4)}",
        )

    def load_state(self, *, thread_hint: str) -> dict[str, Any] | None:
        return load_segment_state(
            path=self.path,
            read_json_best_effort=self.read_json_best_effort,
            state_warnings=self.state_warnings,
            thread_hint=str(thread_hint or "").strip(),
        )

    def persist(self, *, enabled: bool, segment_state: dict[str, Any]) -> None:
        persist_segment_state(
            enabled=bool(enabled),
            path=self.path,
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_max_records=int(self.segment_max_records),
            now_ts=self.now_ts,
            write_json_atomic=self.write_json_atomic,
        )

    def clear(self) -> None:
        clear_segment_state(path=self.path)

    def bootstrap(
        self,
        *,
        enabled: bool,
        continue_hands: bool,
        reset_hands: bool,
        thread_hint: str,
        workflow_marker: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Bootstrap (load or new) segment buffer at run start, then persist it."""

        if not bool(enabled):
            return {}, []

        # When NOT continuing a Hands session, do not carry over an open segment buffer.
        if not bool(continue_hands) or bool(reset_hands):
            self.clear()

        seg0 = self.load_state(thread_hint=str(thread_hint or ""))
        state = seg0 if isinstance(seg0, dict) else self.new_state(reason="run_start", thread_hint=str(thread_hint or ""))
        recs0 = state.get("records")
        records = recs0 if isinstance(recs0, list) else []
        state["records"] = records

        # Include a workflow trigger marker in the segment when present.
        if isinstance(workflow_marker, dict) and workflow_marker:
            records.append(workflow_marker)
            records[:] = records[-int(self.segment_max_records) :]

        self.persist(enabled=True, segment_state=state)
        return state, records

