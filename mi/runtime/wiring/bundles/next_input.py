from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mi.runtime import autopilot as AP
from mi.runtime import prompts as P
import mi.runtime.wiring as W


@dataclass(frozen=True)
class NextInputWiringBundle:
    """Runner wiring bundle for queue_next_input (loop guard + loop break; behavior-preserving)."""

    queue_next_input: Callable[..., bool]


def build_next_input_wiring_bundle(
    *,
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    overlay: dict[str, Any],
    evidence_window: list[dict[str, Any]],
    thread_id_getter: Callable[[], str],
    now_ts: Callable[[], str],
    truncate: Callable[[str, int], str],
    evidence_append: Callable[[dict[str, Any]], Any],
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]],
    read_user_answer: Callable[[str], str],
    append_user_input_record: Callable[..., Any],
    append_segment_record: Callable[[dict[str, Any]], None],
    resolve_ask_when_uncertain: Callable[[], bool],
    checkpoint_before_continue: Callable[..., None],
    get_check_input: Callable[[dict[str, Any] | None], str],
    plan_checks_and_record: Callable[..., tuple[dict[str, Any], str, str]],
    resolve_tls_for_checks: Callable[..., tuple[dict[str, Any], str]],
    empty_check_plan: Callable[[], dict[str, Any]],
    notes_on_skipped: str,
    notes_on_error: str,
    get_sent_sigs: Callable[[], list[str]],
    set_sent_sigs: Callable[[list[str]], None],
    set_next_input: Callable[[str], None],
    set_status: Callable[[str], None],
    set_notes: Callable[[str], None],
) -> NextInputWiringBundle:
    """Build queue_next_input closure used throughout the runner."""

    loop_break_checks_wiring = W.LoopBreakChecksWiringDeps(
        get_check_input=get_check_input,
        plan_checks_and_record=plan_checks_and_record,
        resolve_tls_for_checks=resolve_tls_for_checks,
        empty_check_plan=empty_check_plan,
        notes_on_skipped=str(notes_on_skipped or ""),
        notes_on_error=str(notes_on_error or ""),
    )

    def _loop_break_get_checks_input(**kwargs: Any) -> tuple[str, str]:
        """Wiring adapter for loop-break check computation."""

        return W.loop_break_get_checks_input_wired(
            base_batch_id=str(kwargs.get("base_batch_id") or ""),
            hands_last_message=str(kwargs.get("hands_last_message") or ""),
            thought_db_context=(kwargs.get("thought_db_context") if isinstance(kwargs.get("thought_db_context"), dict) else {}),
            repo_observation=(kwargs.get("repo_observation") if isinstance(kwargs.get("repo_observation"), dict) else {}),
            existing_check_plan=(kwargs.get("existing_check_plan") if isinstance(kwargs.get("existing_check_plan"), dict) else None),
            deps=loop_break_checks_wiring,
        )

    def queue_next_input(
        *,
        nxt: str,
        hands_last_message: str,
        batch_id: str,
        reason: str,
        repo_observation: dict[str, Any] | None = None,
        thought_db_context: dict[str, Any] | None = None,
        check_plan: dict[str, Any] | None = None,
    ) -> bool:
        """Set next_input for the next Hands batch, with loop-guard + loop-break (best-effort)."""

        out = W.queue_next_input_wired(
            nxt=nxt,
            hands_last_message=hands_last_message,
            batch_id=batch_id,
            reason=reason,
            sent_sigs=get_sent_sigs(),
            repo_observation=repo_observation,
            thought_db_context=thought_db_context,
            check_plan=check_plan,
            deps=W.NextInputWiringDeps(
                task=task,
                hands_provider=hands_provider,
                runtime_cfg_getter=runtime_cfg_for_prompts,
                project_overlay=overlay if isinstance(overlay, dict) else {},
                evidence_window=evidence_window,
                thread_id_getter=thread_id_getter,
                loop_sig=AP._loop_sig,
                loop_pattern=AP._loop_pattern,
                now_ts=now_ts,
                truncate=truncate,
                evidence_append=evidence_append,
                append_segment_record=append_segment_record,
                resolve_ask_when_uncertain=resolve_ask_when_uncertain,
                loop_break_prompt_builder=P.loop_break_prompt,
                mind_call=mind_call,
                loop_break_get_checks_input=_loop_break_get_checks_input,
                read_user_answer=read_user_answer,
                append_user_input_record=append_user_input_record,
                checkpoint_before_continue=checkpoint_before_continue,
            ),
        )
        set_sent_sigs(list(out.sent_sigs))
        if not bool(out.queued):
            set_status(str(out.status or "blocked"))
            set_notes(str(out.notes or ""))
            return False
        set_next_input(str(out.next_input or ""))
        set_status(str(out.status or "not_done"))
        set_notes(str(out.notes or ""))
        return True

    return NextInputWiringBundle(queue_next_input=queue_next_input)
