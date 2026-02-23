from __future__ import annotations

from dataclasses import dataclass, field
import secrets
import time
from typing import Any

from . import wiring as W
from . import autopilot as AP
from .autopilot import learn_suggested_flow as LS
from .autopilot import recall_flow as RF
from .autopilot import segment_state as SS
from . import prompts as P
from .runner_wiring_checkpoint import build_checkpoint_mining_wiring_bundle
from .runner_wiring_batch_context import build_batch_context_wiring_bundle
from .runner_wiring_decide import build_decide_wiring_bundle
from .runner_wiring_hands import build_hands_runner_bundle
from .runner_wiring_interaction import build_interaction_record_wiring_bundle
from .runner_wiring_next_input import build_next_input_wiring_bundle
from .runner_wiring_preaction import build_preaction_wiring_bundle
from .runner_wiring_predecide import build_predecide_wiring_bundle
from .runner_wiring_risk import build_risk_predecide_wiring_bundle
from .runner_wiring_testless import build_testless_wiring_bundle
from .runner_wiring_workflow_risk import build_workflow_risk_wiring_bundle
from .runner_helpers import dict_or_empty, get_check_input
from ..core.storage import now_rfc3339, read_json_best_effort, write_json_atomic
from ..thoughtdb import claim_signature
from ..thoughtdb.operational_defaults import resolve_operational_defaults
from ..project.overlay_store import write_project_overlay


@dataclass
class RunnerWiringState:
    """Mutable run state owned by runner_wiring_builder (reduces closure drift)."""

    thread_id: str | None = None
    next_input: str = ""
    status: str = "not_done"
    notes: str = ""
    executed_batches: int = 0
    last_batch_id: str = ""
    last_evidence_rec: dict[str, Any] | None = None
    last_decide_next_rec: dict[str, Any] | None = None

    sent_sigs: list[str] = field(default_factory=list)
    segment_state: dict[str, Any] = field(default_factory=dict)
    segment_records: list[dict[str, Any]] = field(default_factory=list)
    last_checkpoint_key: str = ""


def run_autopilot_from_boot(
    *,
    boot: W.BootstrappedAutopilotRun,
    task: str,
    max_batches: int,
    continue_hands: bool,
    reset_hands: bool,
    why_trace_on_run_end: bool,
    no_mi_prompt: bool,
) -> AP.AutopilotResult:
    """Run the MI autopilot loop after `bootstrap_autopilot_run` (behavior-preserving)."""

    _read_user_answer = boot.run_session.read_user_answer
    project_path = boot.project_path
    home = boot.home
    runtime_cfg = boot.runtime_cfg
    state_warnings = boot.state_warnings

    def _runtime_cfg_for_prompts() -> dict[str, Any]:
        """Runtime knobs context for Mind prompts.

        Canonical values/preferences and operational defaults live in Thought DB Claims.
        This object is only non-canonical runtime knobs (budgets/feature switches).
        """

        return runtime_cfg if isinstance(runtime_cfg, dict) else {}

    # Cross-run Hands session persistence is stored in ProjectOverlay but only used when explicitly enabled.
    overlay = boot.overlay
    hands_state = boot.hands_state
    workflow_run = boot.workflow_run
    _refresh_overlay_refs = boot.refresh_overlay_refs
    cur_provider = boot.cur_provider
    project_paths = boot.project_paths
    wf_store = boot.wf_store
    wf_registry = boot.wf_registry
    mem = boot.mem
    tdb = boot.tdb
    tdb_app = boot.tdb_app
    evw = boot.evw
    llm = boot.llm
    hands_exec = boot.hands_exec
    hands_resume = boot.hands_resume
    run_session = boot.run_session
    _emit_prefixed = boot.emit_prefixed

    evidence_window = boot.evidence_window
    resumed_from_overlay = boot.resumed_from_overlay
    matched = boot.matched_workflow

    state = RunnerWiringState(thread_id=boot.thread_id, next_input=str(boot.next_input or ""))

    def _build_decide_context(*, hands_last_message: str, recent_evidence: list[dict[str, Any]]) -> Any:
        return tdb_app.build_decide_context(
            as_of_ts=now_rfc3339(),
            task=task,
            hands_last_message=hands_last_message,
            recent_evidence=recent_evidence,
        )

    feats = W.parse_runtime_features(runtime_cfg=runtime_cfg, why_trace_on_run_end=bool(why_trace_on_run_end))
    wf_cfg = runtime_cfg.get("workflows") if isinstance(runtime_cfg.get("workflows"), dict) else {}
    pref_cfg = runtime_cfg.get("preference_mining") if isinstance(runtime_cfg.get("preference_mining"), dict) else {}
    wf_auto_mine = bool(feats.wf_auto_mine)
    pref_auto_mine = bool(feats.pref_auto_mine)
    tdb_enabled = bool(feats.tdb_enabled)
    tdb_auto_mine = bool(feats.tdb_auto_mine)
    tdb_auto_nodes = bool(feats.tdb_auto_nodes)
    tdb_min_conf = float(feats.tdb_min_conf)
    tdb_max_claims = int(feats.tdb_max_claims)
    auto_why_on_end = bool(feats.auto_why_on_end)
    why_top_k = int(feats.why_top_k)
    why_min_write_conf = float(feats.why_min_write_conf)
    why_write_edges = bool(feats.why_write_edges)

    # The "segment checkpoint" mechanism is shared infrastructure: it is required for both
    # mining (workflows/preferences/claims) and deterministic node materialization.
    checkpoint_enabled = bool(feats.checkpoint_enabled)

    def _cur_thread_id() -> str:
        return str(state.thread_id or "")

    _flush_state_warnings = W.StateWarningsFlusher(
        state_warnings=state_warnings,
        evidence_append=evw.append,
        now_ts=now_rfc3339,
        thread_id_getter=_cur_thread_id,
        hands_state=hands_state,
    ).flush

    segment_io = W.SegmentStateIO(
        path=project_paths.segment_state_path,
        task=task,
        now_ts=now_rfc3339,
        truncate=AP._truncate,
        read_json_best_effort=read_json_best_effort,
        write_json_atomic=write_json_atomic,
        state_warnings=state_warnings,
        segment_max_records=40,
    )
    segment_max_records = int(segment_io.segment_max_records)
    # Avoid inflating mined occurrence counts within a single `mi run` invocation.
    wf_sigs_counted_in_run: set[str] = set()
    pref_sigs_counted_in_run: set[str] = set()

    def _new_segment_state(*, reason: str, thread_hint: str) -> dict[str, Any]:
        return segment_io.new_state(reason=reason, thread_hint=thread_hint)

    def _persist_segment_state() -> None:
        segment_io.persist(enabled=checkpoint_enabled, segment_state=state.segment_state)

    if checkpoint_enabled:
        seg_state, seg_records = segment_io.bootstrap(
            enabled=True,
            continue_hands=continue_hands,
            reset_hands=reset_hands,
            thread_hint=str(state.thread_id or ""),
            workflow_marker=(evidence_window[-1] if matched else None),
        )
        state.segment_state = seg_state if isinstance(seg_state, dict) else {}
        state.segment_records = seg_records if isinstance(seg_records, list) else []
        _flush_state_warnings()

    interrupt_cfg = feats.interrupt_cfg

    learn_suggested_records_this_run: list[dict[str, Any]] = []

    _mind_call = W.MindCaller(
        llm_call=llm.call,
        evidence_append=evw.append,
        now_ts=now_rfc3339,
        truncate=AP._truncate,
        thread_id_getter=_cur_thread_id,
        evidence_window=evidence_window,
        threshold=2,
    ).call

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
        return evw.append(
            {
                "kind": "decide_next",
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": state.thread_id,
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
                evidence_append=evw.append,
                now_ts=now_rfc3339,
                new_suggestion_id=lambda: f"ls_{time.time_ns()}_{secrets.token_hex(4)}",
                project_id=project_paths.project_id,
                thread_id=str(state.thread_id or ""),
            ),
        )
        if isinstance(rec, dict):
            learn_suggested_records_this_run.append(rec)
        return list(applied_claim_ids)

    def _segment_add(obj: dict[str, Any]) -> None:
        SS.add_segment_record(
            enabled=checkpoint_enabled,
            obj=obj,
            segment_records=state.segment_records,
            segment_max_records=segment_max_records,
            truncate=AP._truncate,
        )

    def _maybe_cross_project_recall(*, batch_id: str, reason: str, query: str) -> None:
        """On-demand cross-project recall (best-effort).

        This writes an EvidenceLog record and appends a compact version to evidence_window so Mind prompts can use it.
        """
        RF.maybe_cross_project_recall_write_through(
            batch_id=batch_id,
            reason=reason,
            query=query,
            thread_id=str(state.thread_id or ""),
            evidence_window=evidence_window,
            deps=RF.RecallDeps(
                mem_recall=mem.maybe_cross_project_recall,
                evidence_append=evw.append,
                segment_add=_segment_add,
                persist_segment_state=_persist_segment_state,
            ),
        )

    testless = build_testless_wiring_bundle(
        project_id=str(project_paths.project_id or ""),
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        overlay=overlay if isinstance(overlay, dict) else {},
        evidence_window=evidence_window,
        tdb=tdb,
        now_ts=now_rfc3339,
        thread_id_getter=lambda: state.thread_id,
        evidence_append=evw.append,
        refresh_overlay_refs=_refresh_overlay_refs,
        write_project_overlay=lambda obj: write_project_overlay(home_dir=home, project_root=project_path, overlay=obj),
        segment_add=_segment_add,
        persist_segment_state=_persist_segment_state,
        read_user_answer=_read_user_answer,
        build_thought_db_context_obj=lambda hlm, recs: _build_decide_context(
            hands_last_message=hlm,
            recent_evidence=recs if isinstance(recs, list) else [],
        ).to_prompt_obj(),
        mind_call=_mind_call,
        empty_check_plan=AP._empty_check_plan,
    )

    W.run_run_start_seeds(
        deps=W.RunStartSeedsDeps(
            home_dir=home,
            tdb=tdb,
            overlay=overlay,
            now_ts=now_rfc3339,
            evidence_append=evw.append,
            mk_testless_strategy_flow_deps=lambda: W.mk_testless_strategy_flow_deps_wired(deps=testless.tls_strategy_wiring),
            maybe_cross_project_recall=_maybe_cross_project_recall,
            task=task,
        )
    )

    _plan_checks_and_record = testless.plan_checks_and_record
    _resolve_tls_for_checks = testless.resolve_tls_for_checks
    _apply_set_testless_strategy_overlay_update = testless.apply_set_testless_strategy_overlay_update

    _get_check_input = get_check_input


    def _get_executed_batches() -> int:
        return int(state.executed_batches)

    def _get_status() -> str:
        return str(state.status or "")

    def _get_notes() -> str:
        return str(state.notes or "")

    def _get_segment_id() -> str:
        if not isinstance(state.segment_state, dict):
            return ""
        return str(state.segment_state.get("segment_id") or "")

    checkpoint_bundle = build_checkpoint_mining_wiring_bundle(
        checkpoint_enabled=bool(checkpoint_enabled),
        wf_auto_mine=bool(wf_auto_mine),
        pref_auto_mine=bool(pref_auto_mine),
        tdb_enabled=bool(tdb_enabled),
        tdb_auto_mine=bool(tdb_auto_mine),
        tdb_auto_nodes=bool(tdb_auto_nodes),
        tdb_min_conf=float(tdb_min_conf),
        tdb_max_claims=int(tdb_max_claims),
        wf_cfg=wf_cfg if isinstance(wf_cfg, dict) else {},
        pref_cfg=pref_cfg if isinstance(pref_cfg, dict) else {},
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        overlay=overlay if isinstance(overlay, dict) else {},
        evidence_window=evidence_window,
        project_paths=project_paths,
        state_warnings=state_warnings,
        flush_state_warnings=_flush_state_warnings,
        wf_registry=wf_registry,
        wf_store=wf_store,
        mem=mem,
        tdb=tdb,
        now_ts=now_rfc3339,
        truncate=AP._truncate,
        thread_id_getter=_cur_thread_id,
        segment_id_getter=_get_segment_id,
        executed_batches_getter=_get_executed_batches,
        status_getter=_get_status,
        notes_getter=_get_notes,
        wf_sigs_counted_in_run=wf_sigs_counted_in_run,
        pref_sigs_counted_in_run=pref_sigs_counted_in_run,
        build_decide_context=_build_decide_context,
        mind_call=_mind_call,
        evidence_append=evw.append,
        handle_learn_suggested=_handle_learn_suggested,
        new_segment_state=_new_segment_state,
    )

    def _maybe_checkpoint_and_mine(*, batch_id: str, planned_next_input: str, status_hint: str, note: str) -> None:
        """LLM-judged checkpoint: may mine workflows/preferences and reset segment buffer."""

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
            _persist_segment_state()
    interaction = build_interaction_record_wiring_bundle(
        evidence_window=evidence_window,
        evidence_append=evw.append,
        segment_add=_segment_add,
        persist_segment_state=_persist_segment_state,
        now_ts=now_rfc3339,
        thread_id_getter=lambda: state.thread_id,
    )

    next_input = build_next_input_wiring_bundle(
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        overlay=overlay if isinstance(overlay, dict) else {},
        evidence_window=evidence_window,
        thread_id_getter=_cur_thread_id,
        now_ts=now_rfc3339,
        truncate=AP._truncate,
        evidence_append=evw.append,
        mind_call=_mind_call,
        read_user_answer=_read_user_answer,
        append_user_input_record=interaction.append_user_input_record,
        append_segment_record=lambda rec: AP.segment_add_and_persist(
            segment_add=_segment_add,
            persist_segment_state=_persist_segment_state,
            item=rec,
        ),
        resolve_ask_when_uncertain=lambda: bool(resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339()).ask_when_uncertain),
        checkpoint_before_continue=_maybe_checkpoint_and_mine,
        get_check_input=_get_check_input,
        plan_checks_and_record=_plan_checks_and_record,
        resolve_tls_for_checks=_resolve_tls_for_checks,
        empty_check_plan=AP._empty_check_plan,
        notes_on_skipped="skipped: mind_circuit_open (plan_min_checks loop_break)",
        notes_on_error="mind_error: plan_min_checks(loop_break) failed; see EvidenceLog kind=mind_error",
        get_sent_sigs=lambda: list(state.sent_sigs),
        set_sent_sigs=lambda xs: setattr(state, "sent_sigs", list(xs)),
        set_next_input=lambda v: setattr(state, "next_input", str(v or "")),
        set_status=lambda v: setattr(state, "status", str(v or "")),
        set_notes=lambda v: setattr(state, "notes", str(v or "")),
    )

    preaction = build_preaction_wiring_bundle(
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        overlay=overlay if isinstance(overlay, dict) else {},
        evidence_window=evidence_window,
        maybe_cross_project_recall=_maybe_cross_project_recall,
        mind_call=_mind_call,
        append_auto_answer_record=interaction.append_auto_answer_record,
        get_check_input=_get_check_input,
        join_hands_inputs=AP.join_hands_inputs,
        queue_next_input=next_input.queue_next_input,
        read_user_answer=_read_user_answer,
        append_user_input_record=interaction.append_user_input_record,
        set_blocked=lambda blocked_note: (
            _set_status("blocked"),
            _set_notes(str(blocked_note or "").strip()),
        ),
        resolve_tls_for_checks=_resolve_tls_for_checks,
    )

    def _set_last_decide_rec(rec: dict[str, Any] | None) -> None:
        state.last_decide_next_rec = rec if isinstance(rec, dict) else None

    decide = build_decide_wiring_bundle(
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        overlay=overlay if isinstance(overlay, dict) else {},
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        workflow_load_effective=wf_registry.load_effective,
        evidence_window=evidence_window,
        build_decide_context=_build_decide_context,
        mind_call=_mind_call,
        log_decide_next=_log_decide_next,
        segment_add=_segment_add,
        persist_segment_state=_persist_segment_state,
        apply_set_testless_strategy_overlay_update=_apply_set_testless_strategy_overlay_update,
        handle_learn_suggested=_handle_learn_suggested,
        emit_prefixed=_emit_prefixed,
        resolve_ask_when_uncertain=lambda: bool(resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339()).ask_when_uncertain),
        looks_like_user_question=AP._looks_like_user_question,
        read_user_answer=_read_user_answer,
        append_user_input_record=interaction.append_user_input_record,
        append_auto_answer_record=interaction.append_auto_answer_record,
        queue_next_input=next_input.queue_next_input,
        maybe_cross_project_recall=_maybe_cross_project_recall,
        get_check_input=_get_check_input,
        join_hands_inputs=AP.join_hands_inputs,
        load_active_workflow=AP.load_active_workflow,
        set_status=lambda v: setattr(state, "status", str(v or "")),
        set_notes=lambda v: setattr(state, "notes", str(v or "")),
        set_last_decide_rec=_set_last_decide_rec,
    )

    hands_runner = build_hands_runner_bundle(
        project_root=project_path,
        transcripts_dir=project_paths.transcripts_dir,
        cur_provider=cur_provider,
        interrupt_cfg=interrupt_cfg,
        overlay=overlay if isinstance(overlay, dict) else {},
        hands_exec=hands_exec,
        hands_resume=hands_resume,
        home_dir=home,
        now_ts=now_rfc3339,
        emit_prefixed=_emit_prefixed,
        evidence_append=evw.append,
        no_mi_prompt=bool(no_mi_prompt),
        get_thread_id=lambda: state.thread_id,
        set_thread_id=lambda v: setattr(state, "thread_id", v),
        get_executed_batches=lambda: int(state.executed_batches),
        set_executed_batches=lambda v: setattr(state, "executed_batches", int(v or 0)),
    )

    def _set_last_evidence_rec(rec: dict[str, Any] | None) -> None:
        state.last_evidence_rec = rec if isinstance(rec, dict) else None

    predecide = build_predecide_wiring_bundle(
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        overlay=overlay if isinstance(overlay, dict) else {},
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        workflow_load_effective=wf_registry.load_effective,
        write_project_overlay=lambda ov: write_project_overlay(home_dir=home, project_root=project_path, overlay=ov),
        evidence_window=evidence_window,
        evidence_append=evw.append,
        segment_add=_segment_add,
        persist_segment_state=_persist_segment_state,
        now_ts=now_rfc3339,
        thread_id_getter=_cur_thread_id,
        build_decide_context=_build_decide_context,
        mind_call=_mind_call,
        emit_prefixed=_emit_prefixed,
        set_last_evidence_rec=_set_last_evidence_rec,
        plan_checks_and_record=_plan_checks_and_record,
        append_auto_answer_record=interaction.append_auto_answer_record,
    )

    def _risk_set_status(value: str) -> None:
        state.status = str(value or "")

    def _risk_set_notes(value: str) -> None:
        state.notes = str(value or "")

    risk = build_risk_predecide_wiring_bundle(
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        overlay=overlay if isinstance(overlay, dict) else {},
        maybe_cross_project_recall=_maybe_cross_project_recall,
        mind_call=_mind_call,
        evidence_window=evidence_window,
        evidence_append=evw.append,
        segment_add=_segment_add,
        persist_segment_state=_persist_segment_state,
        now_ts=now_rfc3339,
        thread_id_getter=_cur_thread_id,
        runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
        read_user_answer=_read_user_answer,
        set_status=_risk_set_status,
        set_notes=_risk_set_notes,
        handle_learn_suggested=_handle_learn_suggested,
    )

    workflow_risk = build_workflow_risk_wiring_bundle(
        apply_workflow_progress=predecide.apply_workflow_progress,
        detect_risk_signals=risk.detect_risk_signals,
        judge_and_handle_risk=risk.judge_and_handle_risk,
    )

    _dict_or_empty = dict_or_empty

    batch_ctx = build_batch_context_wiring_bundle(
        transcripts_dir=project_paths.transcripts_dir,
        tdb=tdb,
        now_ts=now_rfc3339,
        hands_resume=hands_resume,
        resumed_from_overlay=bool(resumed_from_overlay),
        next_input_getter=lambda: str(state.next_input or ""),
        thread_id_getter=lambda: state.thread_id,
    )

    def _run_predecide_via_service(req: AP.BatchRunRequest) -> bool | AP.PreactionDecision:
        out = AP.run_batch_predecide(
            batch_idx=int(req.batch_idx),
            deps=AP.BatchPredecideDeps(
                build_context=batch_ctx.build_context,
                run_hands=hands_runner.run_hands_batch,
                observe_repo=lambda: AP._observe_repo(project_path),
                dict_or_empty=_dict_or_empty,
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
            ),
        )
        state.last_batch_id = str(out.batch_id or f"b{int(req.batch_idx)}")
        return out.out

    def _run_decide_via_service(req: AP.BatchRunRequest, preaction: AP.PreactionDecision) -> bool:
        return decide.run_decide_phase(
            batch_idx=int(req.batch_idx),
            batch_id=str(req.batch_id or f"b{int(req.batch_idx)}"),
            hands_last=str(preaction.hands_last or ""),
            repo_obs=preaction.repo_obs if isinstance(preaction.repo_obs, dict) else {},
            checks_obj=preaction.checks_obj if isinstance(preaction.checks_obj, dict) else {},
            auto_answer_obj=preaction.auto_answer_obj if isinstance(preaction.auto_answer_obj, dict) else AP._empty_auto_answer(),
        )

    def _run_learn_update() -> None:
        AP.maybe_run_learn_update_on_run_end(
            executed_batches=int(state.executed_batches),
            last_batch_id=str(state.last_batch_id or ""),
            learn_suggested_records_this_run=learn_suggested_records_this_run,
            tdb=tdb,
            evw=evw,
            mind_call=_mind_call,
            emit_prefixed=_emit_prefixed,
            truncate=AP._truncate,
            task=task,
            hands_provider=cur_provider,
            runtime_cfg=_runtime_cfg_for_prompts(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            status=str(state.status or ""),
            notes=str(state.notes or ""),
            thread_id=(state.thread_id or ""),
        )

    def _run_why_trace() -> None:
        AP.maybe_run_why_trace_on_run_end(
            enabled=bool(auto_why_on_end),
            executed_batches=int(state.executed_batches),
            last_batch_id=str(state.last_batch_id or ""),
            last_decide_next_rec=state.last_decide_next_rec if isinstance(state.last_decide_next_rec, dict) else None,
            last_evidence_rec=state.last_evidence_rec if isinstance(state.last_evidence_rec, dict) else None,
            tdb=tdb,
            mem_service=mem.service,
            project_paths=project_paths,
            why_top_k=int(why_top_k),
            why_write_edges=bool(why_write_edges),
            why_min_write_conf=float(why_min_write_conf),
            mind_call=_mind_call,
            evw=evw,
            thread_id=(state.thread_id or ""),
        )

    def _set_status(value: str) -> None:
        state.status = str(value or "")

    def _set_notes(value: str) -> None:
        state.notes = str(value or "")

    def _set_last_batch_id(value: str) -> None:
        state.last_batch_id = str(value or "")

    def _run_checkpoint_request(request: Any) -> None:
        _maybe_checkpoint_and_mine(
            batch_id=str(request.batch_id or ""),
            planned_next_input=str(request.planned_next_input or ""),
            status_hint=str(request.status_hint or ""),
            note=str(request.note or ""),
        )

    orchestrator = AP.RunLoopOrchestrator(
        deps=AP.RunLoopOrchestratorDeps(
            max_batches=int(max_batches),
            run_predecide_phase=_run_predecide_via_service,
            run_decide_phase=_run_decide_via_service,
            next_input_getter=lambda: str(state.next_input or ""),
            thread_id_getter=lambda: str(state.thread_id or ""),
            status_getter=lambda: str(state.status or ""),
            status_setter=_set_status,
            notes_getter=lambda: str(state.notes or ""),
            notes_setter=_set_notes,
            last_batch_id_getter=lambda: str(state.last_batch_id or ""),
            last_batch_id_setter=_set_last_batch_id,
            executed_batches_getter=lambda: int(state.executed_batches),
            checkpoint_enabled=bool(checkpoint_enabled),
            checkpoint_runner=_run_checkpoint_request,
            learn_runner=_run_learn_update,
            why_runner=_run_why_trace,
            snapshot_flusher=tdb.flush_snapshots_best_effort,
            state_warning_flusher=_flush_state_warnings,
        )
    )
    orchestrator.run()

    return AP.AutopilotResult(
        status=state.status,
        thread_id=state.thread_id or "unknown",
        project_dir=run_session.project_paths.project_dir,
        evidence_log_path=run_session.project_paths.evidence_log_path,
        transcripts_dir=run_session.project_paths.transcripts_dir,
        batches=state.executed_batches,
        notes=state.notes,
    )
