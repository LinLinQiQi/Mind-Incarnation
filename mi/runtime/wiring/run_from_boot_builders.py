from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from mi.core.storage import now_rfc3339, read_json_best_effort, write_json_atomic
from mi.runtime import autopilot as AP
from mi.runtime.autopilot import learn_suggested_flow as LS
from mi.runtime.autopilot import recall_flow as RF
from mi.runtime.autopilot import segment_state as SS
from mi.runtime.runner_helpers import dict_or_empty
from mi.runtime.runner_state import RunnerStateAccess, RunnerWiringState
from mi.runtime.wiring.mind_call import MindCaller
from mi.runtime.wiring.segments import SegmentStateIO
from mi.thoughtdb import claim_signature


@dataclass(frozen=True)
class CheckpointCallbacks:
    """Checkpoint callbacks used by both the runner loop and loop-break helpers."""

    before_continue: Any
    runner: Any


@dataclass(frozen=True)
class PhaseAssembly:
    """Return type for phase wiring assembly (behavior-preserving)."""

    decide: Any
    batch_predecide_deps: AP.BatchPredecideDeps
    checkpoint_callbacks: CheckpointCallbacks


@dataclass(frozen=True)
class RunEndCallbacks:
    """Run-end callbacks wired into the orchestrator (behavior-preserving)."""

    learn_runner: Callable[[], None]
    why_runner: Callable[[], None]


def build_runtime_cfg_for_prompts(runtime_cfg: Any) -> dict[str, Any]:
    """Return non-canonical runtime knobs for prompts (best-effort)."""

    return runtime_cfg if isinstance(runtime_cfg, dict) else {}


def build_batch_predecide_deps(
    *,
    project_path: Any,
    batch_ctx: Any,
    hands_runner: Any,
    workflow_risk: Any,
    predecide: Any,
    preaction: Any,
) -> AP.BatchPredecideDeps:
    """Build AP.BatchPredecideDeps for AP.run_batch_predecide (behavior-preserving)."""

    return AP.BatchPredecideDeps(
        build_context=batch_ctx.build_context,
        run_hands=hands_runner.run_hands_batch,
        observe_repo=lambda: AP._observe_repo(project_path),
        dict_or_empty=dict_or_empty,
        extract_deps=AP.ExtractEvidenceDeps(extract_context=predecide.extract_evidence_and_context),
        workflow_risk_deps=workflow_risk.deps,
        checks_deps=AP.PlanChecksAutoAnswerDeps(
            plan_checks=predecide.plan_checks,
            maybe_auto_answer=predecide.maybe_auto_answer,
        ),
        preaction_deps=AP.PreactionPhaseDeps(
            apply_preactions=preaction.apply_preactions,
            empty_auto_answer=AP._empty_auto_answer,
        ),
    )


def build_checkpoint_callbacks(
    *,
    checkpoint_bundle: Any,
    state: RunnerWiringState,
    persist_segment_state: Any,
) -> CheckpointCallbacks:
    """Build checkpoint callbacks (behavior-preserving)."""

    def _maybe_checkpoint_and_mine(*, batch_id: str, planned_next_input: str, status_hint: str, note: str) -> None:
        """LLM-judged checkpoint: may mine workflows/preferences/claims and reset segment buffer."""

        res = checkpoint_bundle.run_checkpoint_pipeline(
            segment_state=state.segment_state if isinstance(state.segment_state, dict) else {},
            segment_records=state.segment_records if isinstance(state.segment_records, list) else [],
            last_checkpoint_key=str(state.last_checkpoint_key or ""),
            batch_id=batch_id,
            planned_next_input=planned_next_input,
            status_hint=status_hint,
            note=note,
        )

        state.segment_state = res.segment_state if isinstance(res.segment_state, dict) else {}
        state.segment_records = res.segment_records if isinstance(res.segment_records, list) else []
        state.last_checkpoint_key = str(res.last_checkpoint_key or "")
        if bool(res.persist_segment_state):
            persist_segment_state()

    def _run_checkpoint_request(request: Any) -> None:
        _maybe_checkpoint_and_mine(
            batch_id=str(request.batch_id or ""),
            planned_next_input=str(request.planned_next_input or ""),
            status_hint=str(request.status_hint or ""),
            note=str(request.note or ""),
        )

    return CheckpointCallbacks(before_continue=_maybe_checkpoint_and_mine, runner=_run_checkpoint_request)


def build_segment_state_io(*, project_paths: Any, task: str, state_warnings: list[dict[str, Any]]) -> SegmentStateIO:
    return SegmentStateIO(
        path=project_paths.segment_state_path,
        task=task,
        now_ts=now_rfc3339,
        truncate=AP._truncate,
        read_json_best_effort=read_json_best_effort,
        write_json_atomic=write_json_atomic,
        state_warnings=state_warnings,
        segment_max_records=40,
    )


def bootstrap_segment_state_if_enabled(
    *,
    checkpoint_enabled: bool,
    segment_io: SegmentStateIO,
    continue_hands: bool,
    reset_hands: bool,
    thread_hint: str,
    evidence_window: list[dict[str, Any]],
    matched_workflow: bool,
    state: RunnerWiringState,
    flush_state_warnings: Any,
) -> None:
    if not checkpoint_enabled:
        return
    seg_state, seg_records = segment_io.bootstrap(
        enabled=True,
        continue_hands=continue_hands,
        reset_hands=reset_hands,
        thread_hint=thread_hint,
        workflow_marker=(evidence_window[-1] if matched_workflow else None),
    )
    state.segment_state = seg_state if isinstance(seg_state, dict) else {}
    state.segment_records = seg_records if isinstance(seg_records, list) else []
    flush_state_warnings()


def build_mind_call(
    *,
    llm: Any,
    evidence_append: Any,
    evidence_window: list[dict[str, Any]],
    thread_id_getter: Any,
) -> Any:
    return MindCaller(
        llm_call=llm.call,
        evidence_append=evidence_append,
        now_ts=now_rfc3339,
        truncate=AP._truncate,
        thread_id_getter=thread_id_getter,
        evidence_window=evidence_window,
        threshold=2,
    ).call


def build_run_end_callbacks(
    *,
    enabled_why_trace: bool,
    learn_suggested_records_this_run: list[dict[str, Any]],
    tdb: Any,
    evw: Any,
    mem: Any,
    project_paths: Any,
    why_top_k: int,
    why_write_edges: bool,
    why_min_write_conf: float,
    mind_call: Any,
    emit_prefixed: Any,
    truncate: Any,
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    project_overlay: Any,
    state_access: RunnerStateAccess,
    state: RunnerWiringState,
) -> RunEndCallbacks:
    """Build run-end callbacks (learn update + why trace) for the orchestrator."""

    overlay_obj = dict_or_empty(project_overlay)

    def _run_learn_update() -> None:
        AP.maybe_run_learn_update_on_run_end(
            executed_batches=state_access.get_executed_batches(),
            last_batch_id=state_access.get_last_batch_id(),
            learn_suggested_records_this_run=learn_suggested_records_this_run,
            tdb=tdb,
            evw=evw,
            mind_call=mind_call,
            emit_prefixed=emit_prefixed,
            truncate=truncate,
            task=task,
            hands_provider=hands_provider,
            runtime_cfg=runtime_cfg_for_prompts(),
            project_overlay=overlay_obj,
            status=state_access.get_status(),
            notes=state_access.get_notes(),
            thread_id=state_access.get_thread_id(),
        )

    def _run_why_trace() -> None:
        AP.maybe_run_why_trace_on_run_end(
            enabled=bool(enabled_why_trace),
            executed_batches=state_access.get_executed_batches(),
            last_batch_id=state_access.get_last_batch_id(),
            last_decide_next_rec=state.last_decide_next_rec if isinstance(state.last_decide_next_rec, dict) else None,
            last_evidence_rec=state.last_evidence_rec if isinstance(state.last_evidence_rec, dict) else None,
            tdb=tdb,
            mem_service=mem.service,
            project_paths=project_paths,
            why_top_k=int(why_top_k),
            why_write_edges=bool(why_write_edges),
            why_min_write_conf=float(why_min_write_conf),
            mind_call=mind_call,
            evw=evw,
            thread_id=state_access.get_thread_id(),
        )

    return RunEndCallbacks(learn_runner=_run_learn_update, why_runner=_run_why_trace)


def build_decide_next_logger(
    *,
    evidence_append: Any,
    now_ts: Callable[[], str],
    thread_id_getter: Callable[[], str | None],
) -> Callable[..., dict[str, Any] | None]:
    """Build decide_next EvidenceLog appender (behavior-preserving)."""

    def _log_decide_next(
        *,
        decision_obj: Any,
        batch_id: str,
        phase: str,
        mind_transcript_ref: str,
        thought_db_context_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(decision_obj, dict):
            return None
        return evidence_append(
            {
                "kind": "decide_next",
                "batch_id": batch_id,
                "ts": now_ts(),
                "thread_id": thread_id_getter(),
                "phase": phase,
                "next_action": str(decision_obj.get("next_action") or ""),
                "status": str(decision_obj.get("status") or ""),
                "confidence": decision_obj.get("confidence"),
                "notes": str(decision_obj.get("notes") or ""),
                "ask_user_question": str(decision_obj.get("ask_user_question") or ""),
                "next_hands_input": str(decision_obj.get("next_hands_input") or ""),
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "thought_db": thought_db_context_summary if isinstance(thought_db_context_summary, dict) else {},
                "decision": decision_obj,
            }
        )

    return _log_decide_next


def build_learn_suggested_handler(
    *,
    runtime_cfg: Any,
    project_paths: Any,
    state_access: RunnerStateAccess,
    learn_suggested_records_this_run: list[dict[str, Any]],
    tdb: Any,
    evidence_append: Any,
    now_ts: Callable[[], str],
) -> Callable[..., list[str]]:
    """Build learn_suggested handler (behavior-preserving)."""

    def _handle_learn_suggested(
        *,
        learn_suggested: Any,
        batch_id: str,
        source: str,
        mind_transcript_ref: str,
        source_event_ids: list[str],
    ) -> list[str]:
        applied_claim_ids, rec = LS.apply_learn_suggested(
            learn_suggested=learn_suggested,
            batch_id=batch_id,
            source=source,
            mind_transcript_ref=mind_transcript_ref,
            source_event_ids=source_event_ids,
            runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
            deps=LS.LearnSuggestedDeps(
                claim_signature_fn=claim_signature,
                existing_signature_map=lambda scope: tdb.existing_signature_map(scope=scope),
                append_claim_create=tdb.append_claim_create,
                evidence_append=evidence_append,
                now_ts=now_ts,
                new_suggestion_id=lambda: f"ls_{time.time_ns()}_{secrets.token_hex(4)}",
                project_id=project_paths.project_id,
                thread_id=state_access.get_thread_id(),
            ),
        )
        if isinstance(rec, dict):
            learn_suggested_records_this_run.append(rec)
        return list(applied_claim_ids)

    return _handle_learn_suggested


def build_segment_adder(
    *,
    checkpoint_enabled: bool,
    state: RunnerWiringState,
    segment_max_records: int,
) -> Callable[[dict[str, Any]], None]:
    """Build segment buffer record adder (behavior-preserving)."""

    def _segment_add(obj: dict[str, Any]) -> None:
        SS.add_segment_record(
            enabled=bool(checkpoint_enabled),
            obj=obj,
            segment_records=state.segment_records,
            segment_max_records=int(segment_max_records),
            truncate=AP._truncate,
        )

    return _segment_add


def build_cross_project_recall_writer(
    *,
    mem: Any,
    evidence_append: Any,
    evidence_window: list[dict[str, Any]],
    thread_id_getter: Callable[[], str],
    segment_add: Callable[[dict[str, Any]], None],
    persist_segment_state: Callable[[], None],
) -> Callable[..., None]:
    """Build on-demand cross-project recall writer (behavior-preserving)."""

    def _maybe_cross_project_recall(*, batch_id: str, reason: str, query: str) -> None:
        RF.maybe_cross_project_recall_write_through(
            batch_id=batch_id,
            reason=reason,
            query=query,
            thread_id=thread_id_getter(),
            evidence_window=evidence_window,
            deps=RF.RecallDeps(
                mem_recall=mem.maybe_cross_project_recall,
                evidence_append=evidence_append,
                segment_add=segment_add,
                persist_segment_state=persist_segment_state,
            ),
        )

    return _maybe_cross_project_recall


__all__ = [
    "CheckpointCallbacks",
    "PhaseAssembly",
    "RunEndCallbacks",
    "build_batch_predecide_deps",
    "build_checkpoint_callbacks",
    "build_cross_project_recall_writer",
    "build_decide_next_logger",
    "build_learn_suggested_handler",
    "build_mind_call",
    "build_run_end_callbacks",
    "build_runtime_cfg_for_prompts",
    "build_segment_adder",
    "build_segment_state_io",
    "bootstrap_segment_state_if_enabled",
]
