from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PredecideRetryAutoAnswerDeps:
    """Dependencies for retrying auto_answer after cross-project recall."""

    empty_auto_answer: Callable[[], dict[str, Any]]
    maybe_cross_project_recall: Callable[..., None]
    auto_answer_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    append_auto_answer_record: Callable[..., dict[str, Any]]


def retry_auto_answer_after_recall(
    *,
    batch_idx: int,
    question: str,
    task: str,
    hands_provider: str,
    runtime_cfg: dict[str, Any],
    project_overlay: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    recent_evidence: list[dict[str, Any]],
    deps: PredecideRetryAutoAnswerDeps,
) -> tuple[dict[str, Any], str]:
    """Retry auto_answer after a conservative recall and normalize fallback output."""

    q = str(question or "").strip()
    deps.maybe_cross_project_recall(
        batch_id=f"b{batch_idx}.before_user_recall",
        reason="before_ask_user",
        query=(q + "\n" + task).strip(),
    )
    aa_prompt_retry = deps.auto_answer_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
        hands_last_message=q,
    )
    aa_obj_r, aa_r_ref, aa_r_state = deps.mind_call(
        schema_filename="auto_answer_to_hands.json",
        prompt=aa_prompt_retry,
        tag=f"autoanswer_retry_after_recall_b{batch_idx}",
        batch_id=f"b{batch_idx}.after_recall",
    )
    if aa_obj_r is None:
        aa_retry = deps.empty_auto_answer()
        if str(aa_r_state or "") == "skipped":
            aa_retry["notes"] = "skipped: mind_circuit_open (auto_answer_to_hands retry after recall)"
        else:
            aa_retry["notes"] = "mind_error: auto_answer_to_hands retry failed; see EvidenceLog kind=mind_error"
    else:
        aa_retry = aa_obj_r if isinstance(aa_obj_r, dict) else deps.empty_auto_answer()
    deps.append_auto_answer_record(
        batch_id=f"b{batch_idx}.after_recall",
        mind_transcript_ref=str(aa_r_ref or ""),
        auto_answer=aa_retry if isinstance(aa_retry, dict) else {},
    )
    if isinstance(aa_retry, dict) and bool(aa_retry.get("needs_user_input", False)):
        q2 = str(aa_retry.get("ask_user_question") or "").strip()
        if q2:
            q = q2
    return aa_retry if isinstance(aa_retry, dict) else deps.empty_auto_answer(), q


@dataclass(frozen=True)
class PredecideQueueWithChecksDeps:
    """Dependencies for queuing answer/checks payloads to Hands."""

    get_check_input: Callable[[dict[str, Any] | None], str]
    join_hands_inputs: Callable[[str, str], str]
    queue_next_input: Callable[..., bool]


def try_queue_answer_with_checks(
    *,
    batch_id: str,
    queue_reason: str,
    answer_text: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    deps: PredecideQueueWithChecksDeps,
) -> bool | None:
    """Queue answer + checks when either side has content."""

    check_text = deps.get_check_input(checks_obj if isinstance(checks_obj, dict) else None)
    combined = deps.join_hands_inputs(str(answer_text or "").strip(), check_text)
    if not combined:
        return None
    if not deps.queue_next_input(
        nxt=combined,
        hands_last_message=hands_last,
        batch_id=batch_id,
        reason=queue_reason,
        repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        check_plan=checks_obj if isinstance(checks_obj, dict) else {},
    ):
        return False
    return True


@dataclass(frozen=True)
class PredecidePromptUserDeps:
    """Dependencies for prompt-user fallback path."""

    read_user_answer: Callable[[str], str]
    append_user_input_record: Callable[..., dict[str, Any]]
    set_blocked: Callable[[str], None]
    try_queue_answer_with_checks: Callable[..., bool | None]


def prompt_user_then_queue(
    *,
    batch_idx: int,
    question: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    deps: PredecidePromptUserDeps,
) -> bool:
    """Ask user and queue answer (+ checks), else block when answer missing."""

    answer = deps.read_user_answer(question)
    if not answer:
        deps.set_blocked("user did not provide required input")
        return False
    deps.append_user_input_record(batch_id=f"b{batch_idx}", question=question, answer=answer)

    queued = deps.try_queue_answer_with_checks(
        batch_id=f"b{batch_idx}",
        queue_reason="answered after user input",
        answer_text=answer,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )
    return bool(queued)


@dataclass(frozen=True)
class PredecideNeedsUserDeps:
    """Dependencies for handling pre-decide auto_answer(needs_user_input)."""

    retry_auto_answer_after_recall: Callable[..., tuple[dict[str, Any], str]]
    try_queue_answer_with_checks: Callable[..., bool | None]
    prompt_user_then_queue: Callable[..., bool]


def handle_auto_answer_needs_user(
    *,
    batch_idx: int,
    hands_last: str,
    repo_obs: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    checks_obj: dict[str, Any],
    auto_answer_obj: dict[str, Any],
    deps: PredecideNeedsUserDeps,
) -> tuple[bool, dict[str, Any]]:
    """Handle branch where initial auto_answer requests user input."""

    q = str((auto_answer_obj if isinstance(auto_answer_obj, dict) else {}).get("ask_user_question") or "").strip() or hands_last.strip() or "Need more information:"
    aa_retry, q = deps.retry_auto_answer_after_recall(
        batch_idx=batch_idx,
        question=q,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )

    aa_text = ""
    if isinstance(aa_retry, dict) and bool(aa_retry.get("should_answer", False)):
        aa_text = str(aa_retry.get("hands_answer_input") or "").strip()
    queued_retry = deps.try_queue_answer_with_checks(
        batch_id=f"b{batch_idx}.after_recall",
        queue_reason="auto-answered after cross-project recall",
        answer_text=aa_text,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )
    if isinstance(queued_retry, bool):
        return queued_retry, checks_obj if isinstance(checks_obj, dict) else {}

    asked = deps.prompt_user_then_queue(
        batch_idx=batch_idx,
        question=q,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )
    return asked, checks_obj if isinstance(checks_obj, dict) else {}
