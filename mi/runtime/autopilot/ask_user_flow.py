from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AskUserAutoAnswerAttemptDeps:
    """Dependencies for one ask_user auto-answer attempt."""

    empty_auto_answer: Callable[[], dict[str, Any]]
    build_thought_db_context_obj: Callable[[str, list[dict[str, Any]]], dict[str, Any]]
    auto_answer_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    append_auto_answer_record: Callable[..., dict[str, Any]]
    get_check_input: Callable[[dict[str, Any] | None], str]
    join_hands_inputs: Callable[[str, str], str]
    queue_next_input: Callable[..., bool]


@dataclass(frozen=True)
class DecideAskUserFlowDeps:
    """Dependencies for decide_next(next_action=ask_user) orchestration."""

    run_auto_answer_attempt: Callable[..., tuple[bool | None, str]]
    maybe_cross_project_recall: Callable[..., None]
    read_user_answer: Callable[[str], str]
    append_user_input_record: Callable[..., dict[str, Any]]
    redecide_with_input: Callable[..., bool]
    set_blocked: Callable[[str], None]


def ask_user_auto_answer_attempt(
    *,
    batch_idx: int,
    q: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_obj: dict[str, Any],
    batch_suffix: str,
    tag_suffix: str,
    queue_reason: str,
    note_skipped: str,
    note_error: str,
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    recent_evidence: list[dict[str, Any]],
    deps: AskUserAutoAnswerAttemptDeps,
) -> tuple[bool | None, str]:
    """Try one auto_answer attempt for ask_user; may queue next input immediately."""

    if not q:
        return None, q

    tdb_ctx_aa_obj = deps.build_thought_db_context_obj(q, recent_evidence if isinstance(recent_evidence, list) else [])
    aa_prompt = deps.auto_answer_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_aa_obj if isinstance(tdb_ctx_aa_obj, dict) else {},
        repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
        hands_last_message=q,
    )
    aa_obj, aa_ref, aa_state = deps.mind_call(
        schema_filename="auto_answer_to_hands.json",
        prompt=aa_prompt,
        tag=f"{tag_suffix}_b{batch_idx}",
        batch_id=f"b{batch_idx}.{batch_suffix}",
    )

    aa_out = deps.empty_auto_answer()
    if aa_obj is None:
        if str(aa_state or "") == "skipped":
            aa_out["notes"] = note_skipped
        else:
            aa_out["notes"] = note_error
    else:
        aa_out = aa_obj if isinstance(aa_obj, dict) else deps.empty_auto_answer()

    deps.append_auto_answer_record(
        batch_id=f"b{batch_idx}.{batch_suffix}",
        mind_transcript_ref=str(aa_ref or ""),
        auto_answer=aa_out if isinstance(aa_out, dict) else {},
    )

    aa_text = ""
    if isinstance(aa_out, dict) and bool(aa_out.get("should_answer", False)):
        aa_text = str(aa_out.get("hands_answer_input") or "").strip()
    chk_text = deps.get_check_input(checks_obj if isinstance(checks_obj, dict) else None)
    combined = deps.join_hands_inputs(aa_text, chk_text)
    if combined:
        queued = deps.queue_next_input(
            nxt=combined,
            hands_last_message=hands_last,
            batch_id=f"b{batch_idx}.{batch_suffix}",
            reason=queue_reason,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            thought_db_context=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        )
        return (True if queued else False), q

    if isinstance(aa_out, dict) and bool(aa_out.get("needs_user_input", False)):
        q2 = str(aa_out.get("ask_user_question") or "").strip()
        if q2:
            q = q2
    return None, q


def handle_decide_next_ask_user(
    *,
    batch_idx: int,
    task: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_obj: dict[str, Any],
    decision_obj: dict[str, Any],
    deps: DecideAskUserFlowDeps,
) -> bool:
    """Handle decide_next(next_action=ask_user) with retry-before-prompt behavior."""

    q = str((decision_obj if isinstance(decision_obj, dict) else {}).get("ask_user_question") or "Need more information:").strip()

    r1, q = deps.run_auto_answer_attempt(
        batch_idx=batch_idx,
        q=q,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        batch_suffix="from_decide",
        tag_suffix="autoanswer_from_decide",
        queue_reason="auto-answered instead of prompting user",
        note_skipped="skipped: mind_circuit_open (auto_answer_to_hands from decide_next)",
        note_error="mind_error: auto_answer_to_hands(from decide_next) failed; see EvidenceLog kind=mind_error",
    )
    if isinstance(r1, bool):
        return r1

    deps.maybe_cross_project_recall(
        batch_id=f"b{batch_idx}.from_decide.before_user_recall",
        reason="before_ask_user",
        query=(q + "\n" + task).strip(),
    )
    r2, q = deps.run_auto_answer_attempt(
        batch_idx=batch_idx,
        q=q,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        batch_suffix="from_decide.after_recall",
        tag_suffix="autoanswer_from_decide_after_recall",
        queue_reason="auto-answered (after recall) instead of prompting user",
        note_skipped="skipped: mind_circuit_open (auto_answer_to_hands from decide_next after recall)",
        note_error="mind_error: auto_answer_to_hands(from decide_next after recall) failed; see EvidenceLog kind=mind_error",
    )
    if isinstance(r2, bool):
        return r2

    answer = deps.read_user_answer(q or "Need more information:")
    if not answer:
        deps.set_blocked("user did not provide required input")
        return False
    deps.append_user_input_record(batch_id=f"b{batch_idx}", question=q, answer=answer)

    return deps.redecide_with_input(
        batch_idx=batch_idx,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        answer=answer,
    )
