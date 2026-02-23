from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import autopilot as AP
from . import prompts as P
from . import wiring as W
from .autopilot import decide_actions as AD


@dataclass(frozen=True)
class DecideWiringBundle:
    """Runner wiring bundle for decide_next + ask_user orchestration (behavior-preserving)."""

    run_decide_phase: Callable[..., bool]


def build_decide_wiring_bundle(
    *,
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    overlay: dict[str, Any],
    workflow_run: dict[str, Any],
    workflow_load_effective: Callable[[], list[dict[str, Any]]],
    evidence_window: list[dict[str, Any]],
    build_decide_context: Callable[..., Any],
    mind_call: Callable[..., tuple[Any, str, str]],
    log_decide_next: Callable[..., dict[str, Any] | None],
    segment_add: Callable[[dict[str, Any]], None],
    persist_segment_state: Callable[[], None],
    apply_set_testless_strategy_overlay_update: Callable[..., None],
    handle_learn_suggested: Callable[..., Any],
    emit_prefixed: Callable[[str, str], None],
    resolve_ask_when_uncertain: Callable[[], bool],
    looks_like_user_question: Callable[[str], bool],
    read_user_answer: Callable[[str], str],
    append_user_input_record: Callable[..., dict[str, Any]],
    append_auto_answer_record: Callable[..., dict[str, Any]],
    queue_next_input: Callable[..., bool],
    maybe_cross_project_recall: Callable[..., None],
    get_check_input: Callable[[dict[str, Any] | None], str],
    join_hands_inputs: Callable[[str, str], str],
    load_active_workflow: Callable[..., Any],
    set_status: Callable[[str], None],
    set_notes: Callable[[str], None],
    set_last_decide_rec: Callable[[dict[str, Any] | None], None],
) -> DecideWiringBundle:
    """Build decide_next orchestration wiring + expose a single decide-phase runner."""

    def _build_thought_db_context_obj(hlm: str, recs: list[dict[str, Any]]) -> dict[str, Any]:
        return build_decide_context(
            hands_last_message=str(hlm or ""),
            recent_evidence=recs if isinstance(recs, list) else [],
        ).to_prompt_obj()

    ask_user_auto_answer_wiring = W.AskUserAutoAnswerAttemptWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        recent_evidence=evidence_window,
        empty_auto_answer=AP._empty_auto_answer,
        build_thought_db_context_obj=_build_thought_db_context_obj,
        auto_answer_prompt_builder=P.auto_answer_to_hands_prompt,
        mind_call=mind_call,
        append_auto_answer_record=append_auto_answer_record,
        get_check_input=get_check_input,
        join_hands_inputs=join_hands_inputs,
        queue_next_input=queue_next_input,
    )

    def _ask_user_auto_answer_attempt(
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
    ) -> tuple[bool | None, str]:
        return W.ask_user_auto_answer_attempt_wired(
            batch_idx=batch_idx,
            q=q,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            batch_suffix=batch_suffix,
            tag_suffix=tag_suffix,
            queue_reason=queue_reason,
            note_skipped=note_skipped,
            note_error=note_error,
            deps=ask_user_auto_answer_wiring,
        )

    ask_user_redecide_wiring = W.AskUserRedecideWithInputWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        workflow_load_effective=workflow_load_effective,
        recent_evidence=evidence_window if isinstance(evidence_window, list) else [],
        empty_auto_answer=AP._empty_auto_answer,
        build_decide_context=build_decide_context,
        summarize_thought_db_context=AP.summarize_thought_db_context,
        decide_next_prompt_builder=P.decide_next_prompt,
        load_active_workflow=load_active_workflow,
        mind_call=mind_call,
        log_decide_next=log_decide_next,
        append_decide_record=lambda rec: AP.segment_add_and_persist(
            segment_add=segment_add,
            persist_segment_state=persist_segment_state,
            item=rec,
        ),
        apply_set_testless_strategy_overlay_update=apply_set_testless_strategy_overlay_update,
        handle_learn_suggested=handle_learn_suggested,
        get_check_input=get_check_input,
        join_hands_inputs=join_hands_inputs,
        queue_next_input=queue_next_input,
        set_status=set_status,
        set_notes=set_notes,
    )

    def _ask_user_redecide_with_input(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        answer: str,
    ) -> bool:
        cont, decide_rec2 = W.ask_user_redecide_with_input_wired(
            batch_idx=batch_idx,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            answer=answer,
            deps=ask_user_redecide_wiring,
        )
        if isinstance(decide_rec2, dict) and str(decide_rec2.get("event_id") or "").strip():
            set_last_decide_rec(decide_rec2)
        return bool(cont)

    def _handle_decide_next_ask_user(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_obj: dict[str, Any],
        decision_obj: dict[str, Any],
    ) -> bool:
        return W.handle_decide_next_ask_user_wired(
            batch_idx=batch_idx,
            task=task,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
            deps=W.DecideAskUserWiringDeps(
                maybe_cross_project_recall=maybe_cross_project_recall,
                read_user_answer=read_user_answer,
                append_user_input_record=append_user_input_record,
                set_blocked=lambda blocked_note: (
                    set_status("blocked"),
                    set_notes(str(blocked_note or "").strip()),
                ),
                run_auto_answer_attempt=_ask_user_auto_answer_attempt,
                redecide_with_input=_ask_user_redecide_with_input,
            ),
        )

    decide_next_query_wiring = W.DecideNextQueryWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        workflow_load_effective=workflow_load_effective,
        recent_evidence=evidence_window if isinstance(evidence_window, list) else [],
        build_decide_context=build_decide_context,
        summarize_thought_db_context=AP.summarize_thought_db_context,
        decide_next_prompt_builder=P.decide_next_prompt,
        load_active_workflow=load_active_workflow,
        mind_call=mind_call,
    )

    def _decide_next_query(
        *,
        batch_idx: int,
        batch_id: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        auto_answer_obj: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, str, dict[str, Any], dict[str, Any]]:
        return W.query_decide_next_wired(
            batch_idx=batch_idx,
            batch_id=batch_id,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
            deps=decide_next_query_wiring,
        )

    def _handle_decide_next_missing(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_obj: dict[str, Any],
        decision_state: str,
    ) -> bool:
        cont, blocked_note = AD.handle_decide_next_missing(
            batch_idx=batch_idx,
            decision_state=str(decision_state or ""),
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            ask_when_uncertain=bool(resolve_ask_when_uncertain()),
            looks_like_user_question=looks_like_user_question,
            read_user_answer=read_user_answer,
            append_user_input_record=append_user_input_record,
            queue_next_input=queue_next_input,
        )
        if not cont and blocked_note:
            set_status("blocked")
            set_notes(blocked_note)
        return bool(cont)

    def _decide_next_record_effects(
        *,
        batch_idx: int,
        decision_obj: dict[str, Any],
        decision_mind_ref: str,
        tdb_ctx_summary: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        res = W.record_decide_next_effects_wired(
            batch_idx=batch_idx,
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
            decision_mind_ref=str(decision_mind_ref or ""),
            tdb_ctx_summary=tdb_ctx_summary if isinstance(tdb_ctx_summary, dict) else {},
            deps=W.DecideRecordEffectsWiringDeps(
                log_decide_next=log_decide_next,
                segment_add=segment_add,
                persist_segment_state=persist_segment_state,
                apply_set_testless_strategy_overlay_update=apply_set_testless_strategy_overlay_update,
                handle_learn_suggested=handle_learn_suggested,
                emit_prefixed=emit_prefixed,
            ),
        )
        if isinstance(res.decide_rec, dict) and str(res.decide_rec.get("event_id") or "").strip():
            set_last_decide_rec(res.decide_rec)
        set_status(str(res.status or "not_done"))
        set_notes(str(res.notes or ""))
        return str(res.next_action or "stop"), res.decide_rec if isinstance(res.decide_rec, dict) else None

    def _decide_next_route_action(
        *,
        batch_idx: int,
        next_action: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_obj: dict[str, Any],
        decision_obj: dict[str, Any],
    ) -> bool:
        cont, blocked_note = AD.route_decide_next_action(
            batch_idx=batch_idx,
            next_action=str(next_action or ""),
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
            handle_ask_user=_handle_decide_next_ask_user,
            queue_next_input=queue_next_input,
        )
        if not cont and blocked_note:
            set_status("blocked")
            set_notes(blocked_note)
        return bool(cont)

    def run_decide_phase(
        *,
        batch_idx: int,
        batch_id: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        auto_answer_obj: dict[str, Any],
    ) -> bool:
        return AP.run_decide_next_phase(
            batch_idx=int(batch_idx),
            batch_id=str(batch_id or ""),
            hands_last=str(hands_last or ""),
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
            deps=AP.DecidePhaseDeps(
                query=_decide_next_query,
                handle_missing=_handle_decide_next_missing,
                record_effects=_decide_next_record_effects,
                route_action=_decide_next_route_action,
            ),
        )

    return DecideWiringBundle(run_decide_phase=run_decide_phase)

