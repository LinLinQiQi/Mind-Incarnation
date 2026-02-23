from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mi.runtime import autopilot as AP
from mi.runtime import prompts as P
import mi.runtime.wiring as W


@dataclass(frozen=True)
class PredecideWiringBundle:
    """Runner wiring bundle for the predecide phase (behavior-preserving)."""

    extract_evidence_and_context: Callable[..., tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]]
    apply_workflow_progress: Callable[..., None]
    plan_checks: Callable[..., dict[str, Any]]
    maybe_auto_answer: Callable[..., dict[str, Any]]


def build_predecide_wiring_bundle(
    *,
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    overlay: dict[str, Any],
    workflow_run: dict[str, Any],
    workflow_load_effective: Callable[..., Any],
    write_project_overlay: Callable[[dict[str, Any]], None],
    evidence_window: list[dict[str, Any]],
    evidence_append: Callable[[dict[str, Any]], Any],
    segment_add: Callable[[dict[str, Any]], None],
    persist_segment_state: Callable[[], None],
    now_ts: Callable[[], str],
    thread_id_getter: Callable[[], str],
    build_decide_context: Callable[..., Any],
    mind_call: Callable[..., tuple[Any, str, str]],
    emit_prefixed: Callable[[str, str], None],
    set_last_evidence_rec: Callable[[dict[str, Any] | None], None],
    plan_checks_and_record: Callable[..., tuple[dict[str, Any], str, str]],
    append_auto_answer_record: Callable[..., dict[str, Any]],
) -> PredecideWiringBundle:
    """Build extract/workflow/check/auto-answer wiring used by run_batch_predecide."""

    evidence_record_wiring = W.EvidenceRecordWiringDeps(
        evidence_window=evidence_window,
        evidence_append=evidence_append,
        append_window=AP.append_evidence_window,
        segment_add=segment_add,
        persist_segment_state=persist_segment_state,
        now_ts=now_ts,
        thread_id_getter=thread_id_getter,
    )
    extract_evidence_wiring = W.ExtractEvidenceContextWiringDeps(
        task=task,
        hands_provider=hands_provider,
        batch_summary_fn=AP._batch_summary,
        extract_evidence_prompt_builder=P.extract_evidence_prompt,
        mind_call=mind_call,
        empty_evidence_obj=AP._empty_evidence_obj,
        extract_evidence_counts=AP.extract_evidence_counts,
        emit_prefixed=emit_prefixed,
        evidence_record_deps=evidence_record_wiring,
        build_decide_context=build_decide_context,
    )

    def extract_evidence_and_context(
        *,
        batch_idx: int,
        batch_id: str,
        ctx: AP.BatchExecutionContext,
        result: Any,
        repo_obs: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
        """Run extract_evidence and build Thought DB context for this batch."""

        out = W.extract_evidence_and_context_wired(
            batch_idx=int(batch_idx),
            batch_id=str(batch_id or ""),
            ctx=ctx,
            result=result,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            deps=extract_evidence_wiring,
        )
        set_last_evidence_rec(out.evidence_rec)
        return out.summary, out.evidence_obj, out.hands_last, out.tdb_ctx_batch_obj

    workflow_progress_wiring = W.WorkflowProgressWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        workflow_load_effective=workflow_load_effective,
        load_active_workflow=AP.load_active_workflow,
        workflow_progress_prompt_builder=P.workflow_progress_prompt,
        mind_call=mind_call,
        evidence_append=evidence_append,
        now_ts=now_ts,
        thread_id_getter=thread_id_getter,
        apply_workflow_progress_output_fn=AP.apply_workflow_progress_output,
        write_project_overlay=write_project_overlay,
    )

    def apply_workflow_progress(
        *,
        batch_idx: int,
        batch_id: str,
        summary: dict[str, Any],
        evidence_obj: dict[str, Any],
        repo_obs: dict[str, Any],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
        ctx: AP.BatchExecutionContext,
    ) -> None:
        """Update workflow cursor/state using workflow_progress output (best-effort)."""

        W.apply_workflow_progress_wired(
            batch_idx=batch_idx,
            batch_id=batch_id,
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            last_batch_input=str(ctx.batch_input or ""),
            deps=workflow_progress_wiring,
        )

    def plan_checks(
        *,
        batch_idx: int,
        batch_id: str,
        summary: dict[str, Any],
        evidence_obj: dict[str, Any],
        repo_obs: dict[str, Any],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> dict[str, Any]:
        """Plan minimal checks and emit check-plan log."""

        should_plan_checks = AP._should_plan_checks(
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            hands_last_message=hands_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        )
        checks_obj, _, _ = plan_checks_and_record(
            batch_id=batch_id,
            tag=f"checks_b{batch_idx}",
            thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            should_plan=should_plan_checks,
            notes_on_skip="skipped: no uncertainty/risk/question detected",
            notes_on_skipped="skipped: mind_circuit_open (plan_min_checks)",
            notes_on_error="mind_error: plan_min_checks failed; see EvidenceLog kind=mind_error",
        )
        if isinstance(checks_obj, dict):
            emit_prefixed("[mi]", AP.compose_check_plan_log(checks_obj))
            return checks_obj
        return AP._empty_check_plan()

    auto_answer_query_wiring = W.AutoAnswerQueryWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        recent_evidence=evidence_window,
        auto_answer_prompt_builder=P.auto_answer_to_hands_prompt,
        mind_call=mind_call,
        empty_auto_answer=AP._empty_auto_answer,
    )

    def maybe_auto_answer(
        *,
        batch_idx: int,
        batch_id: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> dict[str, Any]:
        """Auto-answer Hands only when last message looks like a direct question."""

        if not AP._looks_like_user_question(hands_last):
            return AP._empty_auto_answer()

        auto_answer_obj, auto_answer_mind_ref, aa_state = W.query_auto_answer_to_hands_wired(
            batch_idx=batch_idx,
            batch_id=batch_id,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=auto_answer_query_wiring,
        )
        emit_prefixed(
            "[mi]",
            AP.compose_auto_answer_log(
                state=str(aa_state or ""),
                auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
            ),
        )
        append_auto_answer_record(
            batch_id=f"b{batch_idx}",
            mind_transcript_ref=auto_answer_mind_ref,
            auto_answer=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
        )
        return auto_answer_obj if isinstance(auto_answer_obj, dict) else AP._empty_auto_answer()

    return PredecideWiringBundle(
        extract_evidence_and_context=extract_evidence_and_context,
        apply_workflow_progress=apply_workflow_progress,
        plan_checks=plan_checks,
        maybe_auto_answer=maybe_auto_answer,
    )
