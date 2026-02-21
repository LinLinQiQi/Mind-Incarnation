from __future__ import annotations

from typing import Any, Callable


def handle_decide_next_missing(
    *,
    batch_idx: int,
    decision_state: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_obj: dict[str, Any],
    ask_when_uncertain: bool,
    looks_like_user_question: Callable[[str], bool],
    read_user_answer: Callable[[str], str],
    append_user_input_record: Callable[..., Any],
    queue_next_input: Callable[..., bool],
) -> tuple[bool, str]:
    """Fallback when decide_next fails/skips.

    Returns (continue_loop, blocked_note).
    """

    if ask_when_uncertain:
        if decision_state == "skipped":
            if looks_like_user_question(str(hands_last or "")):
                q = str(hands_last or "").strip()
            else:
                q = "MI Mind circuit is OPEN (repeated failures). Provide the next instruction to send to Hands, or type 'stop' to end:"
        else:
            q = "MI Mind failed to decide next action. Provide next instruction to send to Hands, or type 'stop' to end:"
        override = read_user_answer(q)
        append_user_input_record(batch_id=f"b{batch_idx}", question=q, answer=override)

        ov = (override or "").strip()
        if not ov or ov.lower() in ("stop", "quit", "q"):
            note = "stopped after mind_circuit_open(decide_next)" if decision_state == "skipped" else "stopped after mind_error(decide_next)"
            return False, note
        queued = queue_next_input(
            nxt=ov,
            hands_last_message=str(hands_last or ""),
            batch_id=f"b{batch_idx}",
            reason="mind_circuit_open(decide_next): user override" if decision_state == "skipped" else "mind_error(decide_next): user override",
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            thought_db_context=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        )
        return bool(queued), ""

    note = (
        "mind_circuit_open(decide_next): could not proceed (ask_when_uncertain=false)"
        if decision_state == "skipped"
        else "mind_error(decide_next): could not proceed (ask_when_uncertain=false)"
    )
    return False, note


def route_decide_next_action(
    *,
    batch_idx: int,
    next_action: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_obj: dict[str, Any],
    decision_obj: dict[str, Any],
    handle_ask_user: Callable[..., bool],
    queue_next_input: Callable[..., bool],
) -> tuple[bool, str]:
    """Route and apply next_action from decide_next.

    Returns (continue_loop, blocked_note).
    """

    if next_action == "stop":
        return False, ""

    if next_action == "ask_user":
        ok = handle_ask_user(
            batch_idx=batch_idx,
            hands_last=str(hands_last or ""),
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
        )
        return bool(ok), ""

    if next_action == "send_to_hands":
        nxt = str((decision_obj if isinstance(decision_obj, dict) else {}).get("next_hands_input") or "").strip()
        if not nxt:
            return False, "decide_next returned send_to_hands without next_hands_input"
        ok = queue_next_input(
            nxt=nxt,
            hands_last_message=str(hands_last or ""),
            batch_id=f"b{batch_idx}",
            reason="send_to_hands",
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            thought_db_context=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        )
        return bool(ok), ""

    return False, f"unknown next_action={next_action}"
