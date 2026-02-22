from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.mind_call_flow import MindCallDeps, MindCallState, run_mind_call


@dataclass
class MindCaller:
    """Best-effort Mind call helper with circuit-break state (wiring-only).

    The circuit breaker is per `mi run` invocation: after repeated consecutive failures,
    later Mind calls are skipped without raising and without calling the model.
    """

    llm_call: Callable[..., Any]
    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]
    truncate: Callable[[str, int], str]
    thread_id_getter: Callable[[], str]
    evidence_window: list[dict[str, Any]]
    threshold: int = 2

    failures_total: int = 0
    failures_consecutive: int = 0
    circuit_open: bool = False

    def call(
        self,
        *,
        schema_filename: str,
        prompt: str,
        tag: str,
        batch_id: str,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """Call Mind (best-effort) and return (obj, mind_transcript_ref, state)."""

        res = run_mind_call(
            state=MindCallState(
                failures_total=int(self.failures_total),
                failures_consecutive=int(self.failures_consecutive),
                circuit_open=bool(self.circuit_open),
            ),
            thread_id=str(self.thread_id_getter() or ""),
            batch_id=str(batch_id or ""),
            schema_filename=str(schema_filename or ""),
            prompt=str(prompt or ""),
            tag=str(tag or ""),
            threshold=int(self.threshold),
            evidence_window=self.evidence_window,
            deps=MindCallDeps(
                llm_call=self.llm_call,
                evidence_append=self.evidence_append,
                now_ts=self.now_ts,
                truncate=self.truncate,
            ),
        )
        self.failures_total = int(res.next_state.failures_total)
        self.failures_consecutive = int(res.next_state.failures_consecutive)
        self.circuit_open = bool(res.next_state.circuit_open)
        return (
            res.obj if isinstance(res.obj, dict) else None,
            str(res.mind_transcript_ref or ""),
            str(res.state or ""),
        )

