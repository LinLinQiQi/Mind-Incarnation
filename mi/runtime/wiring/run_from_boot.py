from __future__ import annotations

from typing import Any, Callable

import mi.runtime.wiring as W
from mi.runtime import autopilot as AP
from .bundles import (
    build_batch_context_wiring_bundle,
    build_checkpoint_mining_wiring_bundle,
    build_decide_wiring_bundle,
    build_hands_runner_bundle,
    build_interaction_record_wiring_bundle,
    build_next_input_wiring_bundle,
    build_preaction_wiring_bundle,
    build_predecide_wiring_bundle,
    build_risk_predecide_wiring_bundle,
    build_testless_wiring_bundle,
    build_workflow_risk_wiring_bundle,
)
from mi.runtime.runner_helpers import get_check_input
from mi.runtime.composition import build_run_loop_orchestrator
from mi.runtime.runner_state import RunnerStateAccess, RunnerWiringState
from mi.core.storage import now_rfc3339
from mi.thoughtdb.operational_defaults import resolve_operational_defaults
from mi.project.overlay_store import write_project_overlay
from .phase_inputs import normalize_phase_dicts
from .run_from_boot_builders import (
    CheckpointCallbacks,
    PhaseAssembly,
    _bootstrap_segment_state_if_enabled,
    _build_batch_predecide_deps,
    _build_checkpoint_callbacks,
    _build_cross_project_recall_writer,
    _build_decide_next_logger,
    _build_learn_suggested_handler,
    _build_mind_call,
    _build_run_end_callbacks,
    _build_runtime_cfg_for_prompts,
    _build_segment_adder,
    _build_segment_state_io,
)


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
        """Runtime knobs context for Mind prompts (non-canonical; best-effort)."""

        return _build_runtime_cfg_for_prompts(runtime_cfg)

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
    state_access = RunnerStateAccess(state)

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
        return state_access.get_thread_id()

    _flush_state_warnings = W.StateWarningsFlusher(
        state_warnings=state_warnings,
        evidence_append=evw.append,
        now_ts=now_rfc3339,
        thread_id_getter=_cur_thread_id,
        hands_state=hands_state,
    ).flush

    segment_io = _build_segment_state_io(project_paths=project_paths, task=task, state_warnings=state_warnings)
    segment_max_records = int(segment_io.segment_max_records)
    # Avoid inflating mined occurrence counts within a single `mi run` invocation.
    wf_sigs_counted_in_run: set[str] = set()
    pref_sigs_counted_in_run: set[str] = set()

    def _new_segment_state(*, reason: str, thread_hint: str) -> dict[str, Any]:
        return segment_io.new_state(reason=reason, thread_hint=thread_hint)

    def _persist_segment_state() -> None:
        segment_io.persist(enabled=checkpoint_enabled, segment_state=state.segment_state)

    _bootstrap_segment_state_if_enabled(
        checkpoint_enabled=checkpoint_enabled,
        segment_io=segment_io,
        continue_hands=continue_hands,
        reset_hands=reset_hands,
        thread_hint=state_access.get_thread_id(),
        evidence_window=evidence_window,
        matched_workflow=bool(matched),
        state=state,
        flush_state_warnings=_flush_state_warnings,
    )

    interrupt_cfg = feats.interrupt_cfg

    learn_suggested_records_this_run: list[dict[str, Any]] = []

    _mind_call = _build_mind_call(
        llm=llm,
        evidence_append=evw.append,
        evidence_window=evidence_window,
        thread_id_getter=_cur_thread_id,
    )

    _log_decide_next = _build_decide_next_logger(
        evidence_append=evw.append,
        now_ts=now_rfc3339,
        thread_id_getter=state_access.get_thread_id_opt,
    )

    _handle_learn_suggested = _build_learn_suggested_handler(
        runtime_cfg=runtime_cfg,
        project_paths=project_paths,
        state_access=state_access,
        learn_suggested_records_this_run=learn_suggested_records_this_run,
        tdb=tdb,
        evidence_append=evw.append,
        now_ts=now_rfc3339,
    )

    _segment_add = _build_segment_adder(
        checkpoint_enabled=checkpoint_enabled,
        state=state,
        segment_max_records=segment_max_records,
    )

    _maybe_cross_project_recall = _build_cross_project_recall_writer(
        mem=mem,
        evidence_append=evw.append,
        evidence_window=evidence_window,
        thread_id_getter=state_access.get_thread_id,
        segment_add=_segment_add,
        persist_segment_state=_persist_segment_state,
    )

    def _build_phase_bundles() -> PhaseAssembly:
        phase_dicts = normalize_phase_dicts(
            overlay=overlay,
            workflow_run=workflow_run,
            wf_cfg=wf_cfg,
            pref_cfg=pref_cfg,
            runtime_cfg=runtime_cfg,
        )

        def _write_overlay(ov: dict[str, Any]) -> None:
            write_project_overlay(home_dir=home, project_root=project_path, overlay=ov)

        def _resolve_ask_when_uncertain() -> bool:
            return bool(resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339()).ask_when_uncertain)

        testless = build_testless_wiring_bundle(
            project_id=str(project_paths.project_id or ""),
            task=task,
            hands_provider=cur_provider,
            runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
            overlay=phase_dicts.overlay,
            evidence_window=evidence_window,
            tdb=tdb,
            now_ts=now_rfc3339,
            thread_id_getter=state_access.get_thread_id_opt,
            evidence_append=evw.append,
            refresh_overlay_refs=_refresh_overlay_refs,
            write_project_overlay=_write_overlay,
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
                overlay=phase_dicts.overlay,
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
            wf_cfg=phase_dicts.wf_cfg,
            pref_cfg=phase_dicts.pref_cfg,
            task=task,
            hands_provider=cur_provider,
            runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
            overlay=phase_dicts.overlay,
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
            executed_batches_getter=state_access.get_executed_batches,
            status_getter=state_access.get_status,
            notes_getter=state_access.get_notes,
            wf_sigs_counted_in_run=wf_sigs_counted_in_run,
            pref_sigs_counted_in_run=pref_sigs_counted_in_run,
            build_decide_context=_build_decide_context,
            mind_call=_mind_call,
            evidence_append=evw.append,
            handle_learn_suggested=_handle_learn_suggested,
            new_segment_state=_new_segment_state,
        )

        checkpoint_callbacks = _build_checkpoint_callbacks(
            checkpoint_bundle=checkpoint_bundle,
            state=state,
            persist_segment_state=_persist_segment_state,
        )

        interaction = build_interaction_record_wiring_bundle(
            evidence_window=evidence_window,
            evidence_append=evw.append,
            segment_add=_segment_add,
            persist_segment_state=_persist_segment_state,
            now_ts=now_rfc3339,
            thread_id_getter=state_access.get_thread_id_opt,
        )

        next_input = build_next_input_wiring_bundle(
            task=task,
            hands_provider=cur_provider,
            runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
            overlay=phase_dicts.overlay,
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
            resolve_ask_when_uncertain=_resolve_ask_when_uncertain,
            checkpoint_before_continue=checkpoint_callbacks.before_continue,
            get_check_input=get_check_input,
            plan_checks_and_record=_plan_checks_and_record,
            resolve_tls_for_checks=_resolve_tls_for_checks,
            empty_check_plan=AP._empty_check_plan,
            notes_on_skipped="skipped: mind_circuit_open (plan_min_checks loop_break)",
            notes_on_error="mind_error: plan_min_checks(loop_break) failed; see EvidenceLog kind=mind_error",
            get_sent_sigs=state_access.get_sent_sigs,
            set_sent_sigs=state_access.set_sent_sigs,
            set_next_input=state_access.set_next_input,
            set_status=state_access.set_status,
            set_notes=state_access.set_notes,
        )

        preaction = build_preaction_wiring_bundle(
            task=task,
            hands_provider=cur_provider,
            runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
            overlay=phase_dicts.overlay,
            evidence_window=evidence_window,
            maybe_cross_project_recall=_maybe_cross_project_recall,
            mind_call=_mind_call,
            append_auto_answer_record=interaction.append_auto_answer_record,
            get_check_input=get_check_input,
            join_hands_inputs=AP.join_hands_inputs,
            queue_next_input=next_input.queue_next_input,
            read_user_answer=_read_user_answer,
            append_user_input_record=interaction.append_user_input_record,
            set_blocked=lambda blocked_note: (
                state_access.set_status("blocked"),
                state_access.set_notes(str(blocked_note or "").strip()),
            ),
            resolve_tls_for_checks=_resolve_tls_for_checks,
        )

        decide = build_decide_wiring_bundle(
            task=task,
            hands_provider=cur_provider,
            runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
            overlay=phase_dicts.overlay,
            workflow_run=phase_dicts.workflow_run,
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
            resolve_ask_when_uncertain=_resolve_ask_when_uncertain,
            looks_like_user_question=AP._looks_like_user_question,
            read_user_answer=_read_user_answer,
            append_user_input_record=interaction.append_user_input_record,
            append_auto_answer_record=interaction.append_auto_answer_record,
            queue_next_input=next_input.queue_next_input,
            maybe_cross_project_recall=_maybe_cross_project_recall,
            get_check_input=get_check_input,
            join_hands_inputs=AP.join_hands_inputs,
            load_active_workflow=AP.load_active_workflow,
            set_status=state_access.set_status,
            set_notes=state_access.set_notes,
            set_last_decide_rec=state_access.set_last_decide_rec,
        )

        hands_runner = build_hands_runner_bundle(
            project_root=project_path,
            transcripts_dir=project_paths.transcripts_dir,
            cur_provider=cur_provider,
            interrupt_cfg=interrupt_cfg,
            overlay=phase_dicts.overlay,
            hands_exec=hands_exec,
            hands_resume=hands_resume,
            home_dir=home,
            now_ts=now_rfc3339,
            emit_prefixed=_emit_prefixed,
            evidence_append=evw.append,
            no_mi_prompt=bool(no_mi_prompt),
            get_thread_id=state_access.get_thread_id_opt,
            set_thread_id=state_access.set_thread_id,
            get_executed_batches=state_access.get_executed_batches,
            set_executed_batches=state_access.set_executed_batches,
        )

        predecide = build_predecide_wiring_bundle(
            task=task,
            hands_provider=cur_provider,
            runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
            overlay=phase_dicts.overlay,
            workflow_run=phase_dicts.workflow_run,
            workflow_load_effective=wf_registry.load_effective,
            write_project_overlay=_write_overlay,
            evidence_window=evidence_window,
            evidence_append=evw.append,
            segment_add=_segment_add,
            persist_segment_state=_persist_segment_state,
            now_ts=now_rfc3339,
            thread_id_getter=_cur_thread_id,
            build_decide_context=_build_decide_context,
            mind_call=_mind_call,
            emit_prefixed=_emit_prefixed,
            set_last_evidence_rec=state_access.set_last_evidence_rec,
            plan_checks_and_record=_plan_checks_and_record,
            append_auto_answer_record=interaction.append_auto_answer_record,
        )

        risk = build_risk_predecide_wiring_bundle(
            task=task,
            hands_provider=cur_provider,
            runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
            overlay=phase_dicts.overlay,
            maybe_cross_project_recall=_maybe_cross_project_recall,
            mind_call=_mind_call,
            evidence_window=evidence_window,
            evidence_append=evw.append,
            segment_add=_segment_add,
            persist_segment_state=_persist_segment_state,
            now_ts=now_rfc3339,
            thread_id_getter=_cur_thread_id,
            runtime_cfg=phase_dicts.runtime_cfg,
            read_user_answer=_read_user_answer,
            set_status=state_access.set_status,
            set_notes=state_access.set_notes,
            handle_learn_suggested=_handle_learn_suggested,
        )

        workflow_risk = build_workflow_risk_wiring_bundle(
            apply_workflow_progress=predecide.apply_workflow_progress,
            detect_risk_signals=risk.detect_risk_signals,
            judge_and_handle_risk=risk.judge_and_handle_risk,
        )

        batch_ctx = build_batch_context_wiring_bundle(
            transcripts_dir=project_paths.transcripts_dir,
            tdb=tdb,
            now_ts=now_rfc3339,
            hands_resume=hands_resume,
            resumed_from_overlay=bool(resumed_from_overlay),
            next_input_getter=state_access.get_next_input,
            thread_id_getter=state_access.get_thread_id_opt,
        )

        batch_predecide_deps = _build_batch_predecide_deps(
            project_path=project_path,
            batch_ctx=batch_ctx,
            hands_runner=hands_runner,
            workflow_risk=workflow_risk,
            predecide=predecide,
            preaction=preaction,
        )

        return PhaseAssembly(
            decide=decide,
            batch_predecide_deps=batch_predecide_deps,
            checkpoint_callbacks=checkpoint_callbacks,
        )

    assembly = _build_phase_bundles()
    decide = assembly.decide
    batch_predecide_deps = assembly.batch_predecide_deps
    checkpoint_callbacks = assembly.checkpoint_callbacks

    def _run_predecide_via_service(req: AP.BatchRunRequest) -> bool | AP.PreactionDecision:
        out = AP.run_batch_predecide(
            batch_idx=int(req.batch_idx),
            deps=batch_predecide_deps,
        )
        state_access.set_last_batch_id(str(out.batch_id or f"b{int(req.batch_idx)}"))
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

    run_end = _build_run_end_callbacks(
        enabled_why_trace=bool(auto_why_on_end),
        learn_suggested_records_this_run=learn_suggested_records_this_run,
        tdb=tdb,
        evw=evw,
        mem=mem,
        project_paths=project_paths,
        why_top_k=int(why_top_k),
        why_write_edges=bool(why_write_edges),
        why_min_write_conf=float(why_min_write_conf),
        mind_call=_mind_call,
        emit_prefixed=_emit_prefixed,
        truncate=AP._truncate,
        task=task,
        hands_provider=cur_provider,
        runtime_cfg_for_prompts=_runtime_cfg_for_prompts,
        project_overlay=overlay,
        state_access=state_access,
        state=state,
    )

    orchestrator = build_run_loop_orchestrator(
        max_batches=int(max_batches),
        run_predecide_phase=_run_predecide_via_service,
        run_decide_phase=_run_decide_via_service,
        checkpoint_enabled=bool(checkpoint_enabled),
        checkpoint_runner=checkpoint_callbacks.runner,
        learn_runner=run_end.learn_runner,
        why_runner=run_end.why_runner,
        snapshot_flusher=tdb.flush_snapshots_best_effort,
        state_warning_flusher=_flush_state_warnings,
        state=state_access,
    )
    orchestrator.run()

    return AP.AutopilotResult(
        status=state_access.get_status(),
        thread_id=state_access.get_thread_id_opt() or "unknown",
        project_dir=run_session.project_paths.project_dir,
        evidence_log_path=run_session.project_paths.evidence_log_path,
        transcripts_dir=run_session.project_paths.transcripts_dir,
        batches=state_access.get_executed_batches(),
        notes=state_access.get_notes(),
    )
