from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.next_input_flow import (
    LoopGuardDeps,
    LoopGuardResult,
    QueueNextInputDeps,
    QueueNextInputResult,
    apply_loop_guard,
    queue_next_input as run_queue_next_input,
)


@dataclass(frozen=True)
class NextInputWiringDeps:
    """Wiring bundle for next-input queueing (loop guard + loop break)."""

    task: str
    hands_provider: str
    runtime_cfg_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    evidence_window: list[dict[str, Any]]
    thread_id_getter: Callable[[], str]

    loop_sig: Callable[..., str]
    loop_pattern: Callable[[list[str]], str]
    now_ts: Callable[[], str]
    truncate: Callable[[str, int], str]
    evidence_append: Callable[[dict[str, Any]], Any]
    append_segment_record: Callable[[dict[str, Any]], None]
    resolve_ask_when_uncertain: Callable[[], bool]

    loop_break_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    loop_break_get_checks_input: Callable[..., tuple[str, str]]
    read_user_answer: Callable[[str], str]
    append_user_input_record: Callable[..., Any]
    checkpoint_before_continue: Callable[..., None]


def queue_next_input_wired(
    *,
    nxt: str,
    hands_last_message: str,
    batch_id: str,
    reason: str,
    sent_sigs: list[str],
    repo_observation: dict[str, Any] | None,
    thought_db_context: dict[str, Any] | None,
    check_plan: dict[str, Any] | None,
    deps: NextInputWiringDeps,
) -> QueueNextInputResult:
    """Queue next Hands input using runner wiring (behavior-preserving)."""

    def _loop_guard(**kwargs: Any) -> LoopGuardResult:
        return apply_loop_guard(
            candidate=str(kwargs.get("candidate") or ""),
            hands_last_message=str(kwargs.get("hands_last_message") or ""),
            batch_id=str(kwargs.get("batch_id") or ""),
            reason=str(kwargs.get("reason") or ""),
            sent_sigs=kwargs.get("sent_sigs") if isinstance(kwargs.get("sent_sigs"), list) else [],
            task=str(deps.task or ""),
            hands_provider=str(deps.hands_provider or ""),
            runtime_cfg=deps.runtime_cfg_getter() if callable(deps.runtime_cfg_getter) else {},
            project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            check_plan=check_plan if isinstance(check_plan, dict) else {},
            evidence_window=deps.evidence_window if isinstance(deps.evidence_window, list) else [],
            thread_id=str(deps.thread_id_getter() or ""),
            deps=LoopGuardDeps(
                loop_sig=deps.loop_sig,
                loop_pattern=deps.loop_pattern,
                now_ts=deps.now_ts,
                truncate=deps.truncate,
                evidence_append=deps.evidence_append,
                append_segment_record=deps.append_segment_record,
                resolve_ask_when_uncertain=deps.resolve_ask_when_uncertain,
                loop_break_prompt_builder=deps.loop_break_prompt_builder,
                mind_call=deps.mind_call,
                loop_break_get_checks_input=deps.loop_break_get_checks_input,
                read_user_answer=deps.read_user_answer,
                append_user_input_record=deps.append_user_input_record,
            ),
        )

    return run_queue_next_input(
        nxt=nxt,
        hands_last_message=hands_last_message,
        batch_id=batch_id,
        reason=reason,
        sent_sigs=list(sent_sigs),
        deps=QueueNextInputDeps(
            loop_guard=_loop_guard,
            checkpoint_before_continue=deps.checkpoint_before_continue,
        ),
    )

