from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class LoopGuardDeps:
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


@dataclass(frozen=True)
class LoopGuardResult:
    proceed: bool
    candidate: str
    sent_sigs: list[str]
    status: str
    notes: str


def apply_loop_guard(
    *,
    candidate: str,
    hands_last_message: str,
    batch_id: str,
    reason: str,
    sent_sigs: list[str],
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    thought_db_context: dict[str, Any] | None,
    repo_observation: dict[str, Any] | None,
    check_plan: dict[str, Any] | None,
    evidence_window: list[dict[str, Any]],
    thread_id: str,
    deps: LoopGuardDeps,
) -> LoopGuardResult:
    """Apply loop-guard + loop-break policy before queueing next Hands input."""

    sig = deps.loop_sig(hands_last_message=hands_last_message, next_input=candidate)
    sent_sigs2 = list(sent_sigs) + [sig]
    sent_sigs2 = sent_sigs2[-6:]

    pattern = deps.loop_pattern(sent_sigs2)
    if not pattern:
        return LoopGuardResult(proceed=True, candidate=candidate, sent_sigs=sent_sigs2, status="", notes="")

    deps.evidence_append(
        {
            "kind": "loop_guard",
            "batch_id": batch_id,
            "ts": deps.now_ts(),
            "thread_id": thread_id,
            "pattern": pattern,
            "hands_last_message": deps.truncate(hands_last_message, 800),
            "next_input": deps.truncate(candidate, 800),
            "reason": reason,
        }
    )
    evidence_window.append({"kind": "loop_guard", "batch_id": batch_id, "pattern": pattern, "reason": reason})
    evidence_window[:] = evidence_window[-8:]

    ask_when_uncertain = bool(deps.resolve_ask_when_uncertain())

    lb_prompt = deps.loop_break_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
        recent_evidence=evidence_window,
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        loop_pattern=pattern,
        loop_reason=reason,
        hands_last_message=hands_last_message,
        planned_next_input=candidate,
    )
    lb_obj, lb_ref, lb_state = deps.mind_call(
        schema_filename="loop_break.json",
        prompt=lb_prompt,
        tag=f"loopbreak:{batch_id}",
        batch_id=batch_id,
    )

    lb_rec = deps.evidence_append(
        {
            "kind": "loop_break",
            "batch_id": batch_id,
            "ts": deps.now_ts(),
            "thread_id": thread_id,
            "pattern": pattern,
            "reason": reason,
            "state": lb_state,
            "mind_transcript_ref": lb_ref,
            "output": lb_obj if isinstance(lb_obj, dict) else {},
        }
    )
    event_id = str((lb_rec if isinstance(lb_rec, dict) else {}).get("event_id") or "")
    evidence_window.append(
        {
            "kind": "loop_break",
            "batch_id": batch_id,
            "event_id": event_id,
            "pattern": pattern,
            "state": lb_state,
            "action": (lb_obj.get("action") if isinstance(lb_obj, dict) else ""),
            "reason": reason,
        }
    )
    evidence_window[:] = evidence_window[-8:]
    if isinstance(lb_rec, dict):
        deps.append_segment_record(lb_rec)

    action = str(lb_obj.get("action") or "").strip() if isinstance(lb_obj, dict) else ""

    if action == "stop_done":
        return LoopGuardResult(
            proceed=False,
            candidate=candidate,
            sent_sigs=sent_sigs2,
            status="done",
            notes=f"loop_break: stop_done ({reason})",
        )
    if action == "stop_blocked":
        return LoopGuardResult(
            proceed=False,
            candidate=candidate,
            sent_sigs=sent_sigs2,
            status="blocked",
            notes=f"loop_break: stop_blocked ({reason})",
        )

    if action == "rewrite_next_input":
        rewritten = str(lb_obj.get("rewritten_next_input") or "").strip() if isinstance(lb_obj, dict) else ""
        if rewritten:
            candidate = rewritten
            sent_sigs2 = []
        else:
            action = ""

    if action == "run_checks_then_continue":
        chk_text, block_reason = deps.loop_break_get_checks_input(
            base_batch_id=batch_id,
            hands_last_message=hands_last_message,
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            existing_check_plan=check_plan if isinstance(check_plan, dict) else {},
        )
        if block_reason:
            return LoopGuardResult(
                proceed=False,
                candidate=candidate,
                sent_sigs=sent_sigs2,
                status="blocked",
                notes=str(block_reason or ""),
            )
        if chk_text:
            candidate = chk_text
            sent_sigs2 = []
        else:
            action = ""

    def _default_question() -> str:
        return (
            "MI detected a repeated loop (pattern="
            + pattern
            + "). Provide a new instruction to send to Hands, or type 'stop' to end:"
        )

    def _ask_then_apply(q: str) -> LoopGuardResult:
        override = deps.read_user_answer(q)
        deps.append_user_input_record(batch_id=batch_id, question=q, answer=override)
        ov = override.strip()
        if not ov or ov.lower() in ("stop", "quit", "q"):
            return LoopGuardResult(
                proceed=False,
                candidate=candidate,
                sent_sigs=sent_sigs2,
                status="blocked",
                notes="stopped by loop_guard",
            )
        return LoopGuardResult(proceed=True, candidate=ov, sent_sigs=[], status="", notes="")

    if action == "ask_user":
        if ask_when_uncertain:
            q = str(lb_obj.get("ask_user_question") or "").strip() if isinstance(lb_obj, dict) else ""
            q = q or _default_question()
            return _ask_then_apply(q)
        return LoopGuardResult(
            proceed=False,
            candidate=candidate,
            sent_sigs=sent_sigs2,
            status="blocked",
            notes="loop_guard triggered (ask_when_uncertain=false)",
        )

    if not action:
        if ask_when_uncertain:
            return _ask_then_apply(_default_question())
        return LoopGuardResult(
            proceed=False,
            candidate=candidate,
            sent_sigs=sent_sigs2,
            status="blocked",
            notes="loop_guard triggered",
        )

    return LoopGuardResult(proceed=True, candidate=candidate, sent_sigs=sent_sigs2, status="", notes="")
