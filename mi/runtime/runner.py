from __future__ import annotations

import json
import sys
import secrets
import time
from typing import Any

from .wiring import (
    CheckpointWiringDeps,
    MindCaller,
    RunStartSeedsDeps,
    SegmentStateIO,
    StateWarningsFlusher,
    bootstrap_autopilot_run,
    parse_runtime_features,
    run_checkpoint_pipeline_wired,
    run_run_start_seeds,
)
from .autopilot import (
    AutopilotResult,
    _batch_summary,
    _detect_risk_signals,
    _detect_risk_signals_from_transcript,
    _empty_auto_answer,
    _empty_check_plan,
    _empty_evidence_obj,
    _looks_like_user_question,
    _loop_pattern,
    _loop_sig,
    _observe_repo,
    _should_plan_checks,
    _truncate,
    apply_workflow_progress_output,
    BatchExecutionContext,
    build_batch_execution_context,
    append_evidence_window,
    segment_add_and_persist,
    extract_evidence_counts,
    build_risk_fallback,
    should_prompt_risk_user,
    PreactionDecision,
    join_hands_inputs,
    compose_check_plan_log,
    compose_auto_answer_log,
    load_active_workflow,
    maybe_run_learn_update_on_run_end,
    maybe_run_why_trace_on_run_end,
    summarize_thought_db_context,
    RunState,
    RunDeps,
    HandsFlowDeps,
    run_hands_batch,
    DecidePhaseDeps,
    run_decide_next_phase,
    PlanChecksAutoAnswerDeps,
    run_plan_checks_and_auto_answer,
    WorkflowRiskPhaseDeps,
    run_workflow_and_risk_phase,
    RunLoopOrchestrator,
    RunLoopOrchestratorDeps,
    BatchRunRequest,
    ExtractEvidenceDeps,
    PreactionPhaseDeps,
    BatchPredecideDeps,
    run_batch_predecide,
)
from .autopilot.services import (
    find_testless_strategy_claim,
    parse_testless_strategy_from_claim_text,
    upsert_testless_strategy_claim,
)
from .autopilot.decide_actions import (
    handle_decide_next_missing,
    route_decide_next_action,
)
from .autopilot.decide_query_flow import (
    DecideNextQueryDeps,
    DecideRecordEffectsDeps,
    query_decide_next as run_query_decide_next,
    record_decide_next_effects as run_record_decide_next_effects,
)
from .autopilot.risk_predecide import (
    RiskPredecideDeps,
    maybe_prompt_risk_continue,
    query_risk_judge,
    run_risk_predecide,
)
from .autopilot.checkpoint_mining import (
    WorkflowMiningDeps,
    PreferenceMiningDeps,
    mine_workflow_from_segment as run_workflow_mining,
    mine_preferences_from_segment as run_preference_mining,
)
from .autopilot.claim_mining_flow import (
    ClaimMiningDeps,
    mine_claims_from_segment as run_claim_mining,
)
from .autopilot.node_materialize import (
    NodeMaterializeDeps,
    materialize_nodes_from_checkpoint,
)
from .autopilot.next_input_flow import (
    LoopGuardDeps,
    apply_loop_guard,
    QueueNextInputDeps,
    queue_next_input as run_queue_next_input,
)
from .autopilot.loop_break_checks_flow import (
    LoopBreakChecksDeps,
    loop_break_get_checks_input as run_loop_break_get_checks_input,
)
from .autopilot.learn_suggested_flow import (
    LearnSuggestedDeps,
    apply_learn_suggested,
)
from .autopilot.auto_answer_flow import (
    AutoAnswerQueryDeps,
    query_auto_answer_to_hands,
)
from .autopilot.recall_flow import (
    RecallDeps,
    maybe_cross_project_recall_write_through as run_cross_project_recall_write_through,
)
from .autopilot.interaction_record_flow import (
    InteractionRecordDeps,
    append_auto_answer_record_with_tracking as run_append_auto_answer_record,
    append_user_input_record_with_tracking as run_append_user_input_record,
)
from .autopilot.ask_user_flow import (
    AskUserAutoAnswerAttemptDeps,
    AskUserRedecideDeps,
    DecideAskUserFlowDeps,
    ask_user_redecide_with_input as run_ask_user_redecide_with_input,
    ask_user_auto_answer_attempt as run_ask_user_auto_answer_attempt,
    handle_decide_next_ask_user as run_handle_decide_next_ask_user,
)
from .autopilot.predecide_user_flow import (
    PredecideNeedsUserDeps,
    PredecidePromptUserDeps,
    PredecideQueueWithChecksDeps,
    PredecideRetryAutoAnswerDeps,
    handle_auto_answer_needs_user as run_predecide_handle_auto_answer_needs_user,
    prompt_user_then_queue as run_predecide_prompt_user_then_queue,
    retry_auto_answer_after_recall as run_predecide_retry_auto_answer_after_recall,
    try_queue_answer_with_checks as run_predecide_try_queue_answer_with_checks,
)
from .autopilot.check_plan_flow import (
    CheckPlanFlowDeps,
    append_check_plan_record_with_tracking,
    call_plan_min_checks,
    plan_checks_and_record,
)
from .autopilot.evidence_flow import (
    EvidenceAppendDeps,
    append_evidence_with_tracking,
)
from .autopilot.risk_event_flow import (
    RiskEventAppendDeps,
    append_risk_event_with_tracking,
)
from .autopilot.testless_strategy_flow import (
    TestlessStrategyFlowDeps,
    TestlessResolutionDeps,
    apply_set_testless_strategy_overlay_update,
    canonicalize_tls_and_update_overlay,
    resolve_tls_for_checks,
    sync_tls_overlay_from_thoughtdb,
)
from .autopilot.workflow_progress_flow import (
    WorkflowProgressQueryDeps,
    apply_workflow_progress_and_persist,
    append_workflow_progress_event,
    build_workflow_progress_latest_evidence,
    query_workflow_progress,
)
from .autopilot.segment_state import (
    add_segment_record,
)
from .prompts import (
    checkpoint_decide_prompt,
    decide_next_prompt,
    extract_evidence_prompt,
    loop_break_prompt,
    plan_min_checks_prompt,
    workflow_progress_prompt,
)
from .prompts import auto_answer_to_hands_prompt
from .prompts import risk_judge_prompt
from .prompts import suggest_workflow_prompt, mine_preferences_prompt, mine_claims_prompt
from ..core.storage import append_jsonl, now_rfc3339, read_json_best_effort, write_json_atomic
from ..workflows import (
    load_workflow_candidates,
    write_workflow_candidates,
    new_workflow_id,
)
from ..workflows.preferences import load_preference_candidates, write_preference_candidates, preference_signature
from ..workflows.hosts import sync_hosts_from_overlay
from ..memory.ingest import thoughtdb_node_item
from .injection import build_light_injection
from ..thoughtdb import ThoughtDbStore, claim_signature
from ..thoughtdb.app_service import ThoughtDbApplicationService
from ..thoughtdb.operational_defaults import resolve_operational_defaults
from ..project.overlay_store import load_project_overlay, write_project_overlay


_DEFAULT = object()


def _read_user_answer(question: str) -> str:
    print(question.strip(), file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def run_autopilot(
    *,
    task: str,
    project_root: str,
    home_dir: str | None,
    max_batches: int,
    hands_exec: Any | None = None,
    hands_resume: Any = _DEFAULT,
    llm: Any | None = None,
    hands_provider: str = "",
    continue_hands: bool = False,
    reset_hands: bool = False,
    why_trace_on_run_end: bool = False,
    live: bool = False,
    quiet: bool = False,
    no_mi_prompt: bool = False,
    redact: bool = False,
) -> AutopilotResult:
    boot = bootstrap_autopilot_run(
        task=task,
        project_root=project_root,
        home_dir=home_dir,
        hands_provider=hands_provider,
        continue_hands=continue_hands,
        reset_hands=reset_hands,
        llm=llm,
        hands_exec=hands_exec,
        hands_resume=hands_resume,
        hands_resume_default_sentinel=_DEFAULT,
        live=live,
        quiet=quiet,
        redact=redact,
        read_user_answer=_read_user_answer,
    )
    project_path = boot.project_path
    home = boot.home
    runtime_cfg = boot.runtime_cfg
    state_warnings = boot.state_warnings

    def _mindspec_base_runtime() -> dict[str, Any]:
        """Runtime knobs context for Mind prompts.

        Historical name: "MindSpec base". Canonical values/preferences and operational defaults
        are in Thought DB Claims; this object is only runtime knobs (budgets/feature switches).
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
    thread_id = boot.thread_id
    resumed_from_overlay = boot.resumed_from_overlay
    next_input = boot.next_input
    matched = boot.matched_workflow

    status = "not_done"
    notes = ""

    def _build_decide_context(*, hands_last_message: str, recent_evidence: list[dict[str, Any]]) -> Any:
        return tdb_app.build_decide_context(
            as_of_ts=now_rfc3339(),
            task=task,
            hands_last_message=hands_last_message,
            recent_evidence=recent_evidence,
        )

    feats = parse_runtime_features(runtime_cfg=runtime_cfg, why_trace_on_run_end=bool(why_trace_on_run_end))
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
        return str(thread_id or "")

    _flush_state_warnings = StateWarningsFlusher(
        state_warnings=state_warnings,
        evidence_append=evw.append,
        now_ts=now_rfc3339,
        thread_id_getter=_cur_thread_id,
        hands_state=hands_state,
    ).flush

    segment_io = SegmentStateIO(
        path=project_paths.segment_state_path,
        task=task,
        now_ts=now_rfc3339,
        truncate=_truncate,
        read_json_best_effort=read_json_best_effort,
        write_json_atomic=write_json_atomic,
        state_warnings=state_warnings,
        segment_max_records=40,
    )
    segment_max_records = int(segment_io.segment_max_records)
    segment_state: dict[str, Any] = {}
    segment_records: list[dict[str, Any]] = []
    # Avoid inflating mined occurrence counts within a single `mi run` invocation.
    wf_sigs_counted_in_run: set[str] = set()
    pref_sigs_counted_in_run: set[str] = set()

    def _new_segment_state(*, reason: str, thread_hint: str) -> dict[str, Any]:
        return segment_io.new_state(reason=reason, thread_hint=thread_hint)

    def _persist_segment_state() -> None:
        segment_io.persist(enabled=checkpoint_enabled, segment_state=segment_state)

    if checkpoint_enabled:
        segment_state, segment_records = segment_io.bootstrap(
            enabled=True,
            continue_hands=continue_hands,
            reset_hands=reset_hands,
            thread_hint=str(thread_id or ""),
            workflow_marker=(evidence_window[-1] if matched else None),
        )
        _flush_state_warnings()

    interrupt_cfg = feats.interrupt_cfg

    sent_sigs: list[str] = []
    learn_suggested_records_this_run: list[dict[str, Any]] = []

    _mind_call = MindCaller(
        llm_call=llm.call,
        evidence_append=evw.append,
        now_ts=now_rfc3339,
        truncate=_truncate,
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
                "thread_id": thread_id,
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
        applied_claim_ids, rec = apply_learn_suggested(
            learn_suggested=learn_suggested,
            batch_id=batch_id,
            source=source,
            mind_transcript_ref=mind_transcript_ref,
            source_event_ids=source_event_ids,
            runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
            deps=LearnSuggestedDeps(
                claim_signature_fn=claim_signature,
                existing_signature_map=lambda scope: tdb.existing_signature_map(scope=scope),
                append_claim_create=tdb.append_claim_create,
                evidence_append=evw.append,
                now_ts=now_rfc3339,
                new_suggestion_id=lambda: f"ls_{time.time_ns()}_{secrets.token_hex(4)}",
                project_id=project_paths.project_id,
                thread_id=str(thread_id or ""),
            ),
        )
        if isinstance(rec, dict):
            learn_suggested_records_this_run.append(rec)
        return list(applied_claim_ids)

    def _segment_add(obj: dict[str, Any]) -> None:
        add_segment_record(
            enabled=checkpoint_enabled,
            obj=obj,
            segment_records=segment_records,
            segment_max_records=segment_max_records,
            truncate=_truncate,
        )

    def _maybe_cross_project_recall(*, batch_id: str, reason: str, query: str) -> None:
        """On-demand cross-project recall (best-effort).

        This writes an EvidenceLog record and appends a compact version to evidence_window so Mind prompts can use it.
        """
        run_cross_project_recall_write_through(
            batch_id=batch_id,
            reason=reason,
            query=query,
            thread_id=str(thread_id or ""),
            evidence_window=evidence_window,
            deps=RecallDeps(
                mem_recall=mem.maybe_cross_project_recall,
                evidence_append=evw.append,
                segment_add=_segment_add,
                persist_segment_state=_persist_segment_state,
            ),
        )

    def _parse_testless_strategy_from_claim_text(text: str) -> str:
        return parse_testless_strategy_from_claim_text(text)

    def _find_testless_strategy_claim(*, as_of_ts: str) -> dict[str, Any] | None:
        return find_testless_strategy_claim(tdb=tdb, as_of_ts=as_of_ts)

    def _upsert_testless_strategy_claim(*, strategy_text: str, source_event_id: str, source: str, rationale: str) -> str:
        return upsert_testless_strategy_claim(
            tdb=tdb,
            project_id=project_paths.project_id,
            strategy_text=strategy_text,
            source_event_id=source_event_id,
            source=source,
            rationale=rationale,
        )

    def _mk_testless_strategy_flow_deps() -> TestlessStrategyFlowDeps:
        """Build TestlessStrategyFlowDeps with shared overlay IO/service hooks."""

        return TestlessStrategyFlowDeps(
            now_ts=now_rfc3339,
            thread_id=thread_id,
            evidence_append=evw.append,
            find_testless_strategy_claim=lambda ts: _find_testless_strategy_claim(as_of_ts=ts),
            parse_testless_strategy_from_claim_text=_parse_testless_strategy_from_claim_text,
            upsert_testless_strategy_claim=_upsert_testless_strategy_claim,
            write_overlay=lambda obj: write_project_overlay(home_dir=home, project_root=project_path, overlay=obj),
            refresh_overlay_refs=_refresh_overlay_refs,
        )

    run_run_start_seeds(
        deps=RunStartSeedsDeps(
            home_dir=home,
            tdb=tdb,
            overlay=overlay,
            now_ts=now_rfc3339,
            evidence_append=evw.append,
            mk_testless_strategy_flow_deps=_mk_testless_strategy_flow_deps,
            maybe_cross_project_recall=_maybe_cross_project_recall,
            task=task,
        )
    )

    def _mk_check_plan_flow_deps() -> CheckPlanFlowDeps:
        """Build CheckPlanFlowDeps once per call-site; keeps runner wiring consistent."""

        return CheckPlanFlowDeps(
            empty_check_plan=_empty_check_plan,
            evidence_append=evw.append,
            segment_add=_segment_add,
            persist_segment_state=_persist_segment_state,
            now_ts=now_rfc3339,
            thread_id=thread_id,
            plan_min_checks_prompt_builder=plan_min_checks_prompt,
            mind_call=_mind_call,
        )

    def _mk_testless_resolution_deps() -> TestlessResolutionDeps:
        """Build TestlessResolutionDeps for ask-once TLS resolution + replan path."""

        return TestlessResolutionDeps(
            now_ts=now_rfc3339,
            thread_id=thread_id,
            read_user_answer=_read_user_answer,
            evidence_append=evw.append,
            segment_add=lambda item: _segment_add(item if isinstance(item, dict) else {}),
            persist_segment_state=_persist_segment_state,
            sync_tls_overlay=lambda ts: _sync_tls_overlay_from_thoughtdb(as_of_ts=ts),
            canonicalize_tls=lambda **kwargs: _canonicalize_tls_and_update_overlay(
                strategy_text=str(kwargs.get("strategy_text") or ""),
                source_event_id=str(kwargs.get("source_event_id") or ""),
                fallback_batch_id=str(kwargs.get("fallback_batch_id") or ""),
                overlay_rationale=str(kwargs.get("overlay_rationale") or ""),
                overlay_rationale_default=str(kwargs.get("overlay_rationale_default") or ""),
                claim_rationale=str(kwargs.get("claim_rationale") or ""),
                default_rationale=str(kwargs.get("default_rationale") or ""),
                source=str(kwargs.get("source") or ""),
            ),
            build_thought_db_context_obj=lambda hlm, recs: _build_decide_context(
                hands_last_message=hlm,
                recent_evidence=recs if isinstance(recs, list) else [],
            ).to_prompt_obj(),
            plan_checks_and_record=lambda **kwargs: _plan_checks_and_record(
                batch_id=str(kwargs.get("batch_id") or ""),
                tag=str(kwargs.get("tag") or ""),
                thought_db_context=(kwargs.get("thought_db_context") if isinstance(kwargs.get("thought_db_context"), dict) else {}),
                repo_observation=(kwargs.get("repo_observation") if isinstance(kwargs.get("repo_observation"), dict) else {}),
                should_plan=bool(kwargs.get("should_plan")),
                notes_on_skip=str(kwargs.get("notes_on_skip") or ""),
                notes_on_skipped=str(kwargs.get("notes_on_skipped") or ""),
                notes_on_error=str(kwargs.get("notes_on_error") or ""),
            ),
            plan_checks_and_record2=lambda **kwargs: _plan_checks_and_record2(
                batch_id=str(kwargs.get("batch_id") or ""),
                tag=str(kwargs.get("tag") or ""),
                thought_db_context=(kwargs.get("thought_db_context") if isinstance(kwargs.get("thought_db_context"), dict) else {}),
                repo_observation=(kwargs.get("repo_observation") if isinstance(kwargs.get("repo_observation"), dict) else {}),
                should_plan=bool(kwargs.get("should_plan")),
                notes_on_skip=str(kwargs.get("notes_on_skip") or ""),
                notes_on_skipped=str(kwargs.get("notes_on_skipped") or ""),
                notes_on_error=str(kwargs.get("notes_on_error") or ""),
                postprocess=kwargs.get("postprocess"),
            ),
            empty_check_plan=_empty_check_plan,
        )

    def _append_check_plan_record(*, batch_id: str, checks_obj: Any, mind_transcript_ref: str) -> dict[str, Any]:
        """Append a check_plan record and keep evidence_window/segment in sync (single source of truth)."""
        return append_check_plan_record_with_tracking(
            batch_id=batch_id,
            checks_obj=checks_obj,
            mind_transcript_ref=mind_transcript_ref,
            evidence_window=evidence_window,
            deps=_mk_check_plan_flow_deps(),
        )

    def _get_check_input(checks_obj: dict[str, Any] | None) -> str:
        """Return hands_check_input when should_run_checks=true (best-effort)."""

        if not isinstance(checks_obj, dict):
            return ""
        if not bool(checks_obj.get("should_run_checks", False)):
            return ""
        return str(checks_obj.get("hands_check_input") or "").strip()

    def _call_plan_min_checks(
        *,
        batch_id: str,
        tag: str,
        thought_db_context: dict[str, Any],
        repo_observation: dict[str, Any],
        notes_on_skipped: str,
        notes_on_error: str,
    ) -> tuple[dict[str, Any], str, str]:
        """Call plan_min_checks and normalize failure into an empty plan with notes."""
        return call_plan_min_checks(
            batch_id=batch_id,
            tag=tag,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            recent_evidence=evidence_window,
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            notes_on_skipped=notes_on_skipped,
            notes_on_error=notes_on_error,
            deps=_mk_check_plan_flow_deps(),
        )

    def _plan_checks_and_record2(
        *,
        batch_id: str,
        tag: str,
        thought_db_context: dict[str, Any],
        repo_observation: dict[str, Any],
        should_plan: bool,
        notes_on_skip: str,
        notes_on_skipped: str,
        notes_on_error: str,
        postprocess: Any | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """Plan minimal checks and always record a check_plan event (best-effort).

        Optionally applies a small postprocess hook to normalize the recorded check plan
        (e.g., avoid re-prompting for testless strategy when Thought DB already has one).
        """

        return plan_checks_and_record(
            batch_id=batch_id,
            tag=tag,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            recent_evidence=evidence_window,
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            should_plan=bool(should_plan),
            notes_on_skip=notes_on_skip,
            notes_on_skipped=notes_on_skipped,
            notes_on_error=notes_on_error,
            evidence_window=evidence_window,
            postprocess=postprocess,
            deps=_mk_check_plan_flow_deps(),
        )

    def _plan_checks_and_record(
        *,
        batch_id: str,
        tag: str,
        thought_db_context: dict[str, Any],
        repo_observation: dict[str, Any],
        should_plan: bool,
        notes_on_skip: str,
        notes_on_skipped: str,
        notes_on_error: str,
    ) -> tuple[dict[str, Any], str, str]:
        """Plan minimal checks and always record a check_plan event (best-effort)."""
        return _plan_checks_and_record2(
            batch_id=batch_id,
            tag=tag,
            thought_db_context=thought_db_context,
            repo_observation=repo_observation,
            should_plan=should_plan,
            notes_on_skip=notes_on_skip,
            notes_on_skipped=notes_on_skipped,
            notes_on_error=notes_on_error,
            postprocess=None,
        )

    def _sync_tls_overlay_from_thoughtdb(*, as_of_ts: str) -> tuple[str, str, bool]:
        """Sync canonical testless strategy claim -> derived overlay pointer (best-effort)."""

        nonlocal overlay

        return sync_tls_overlay_from_thoughtdb(
            overlay=overlay if isinstance(overlay, dict) else {},
            as_of_ts=as_of_ts,
            deps=_mk_testless_strategy_flow_deps(),
        )

    def _canonicalize_tls_and_update_overlay(
        *,
        strategy_text: str,
        source_event_id: str,
        fallback_batch_id: str,
        overlay_rationale: str,
        overlay_rationale_default: str,
        claim_rationale: str,
        default_rationale: str,
        source: str,
    ) -> str:
        """Canonicalize a testless strategy into Thought DB and mirror a pointer into ProjectOverlay (best-effort)."""

        nonlocal overlay

        return canonicalize_tls_and_update_overlay(
            overlay=overlay if isinstance(overlay, dict) else {},
            strategy_text=strategy_text,
            source_event_id=source_event_id,
            fallback_batch_id=fallback_batch_id,
            overlay_rationale=overlay_rationale,
            overlay_rationale_default=overlay_rationale_default,
            claim_rationale=claim_rationale,
            default_rationale=default_rationale,
            source=source,
            deps=_mk_testless_strategy_flow_deps(),
        )

    def _resolve_tls_for_checks(
        *,
        checks_obj: dict[str, Any],
        hands_last_message: str,
        repo_observation: dict[str, Any],
        user_input_batch_id: str,
        batch_id_after_testless: str,
        batch_id_after_tls_claim: str,
        tag_after_testless: str,
        tag_after_tls_claim: str,
        notes_prefix: str,
        source: str,
        rationale: str,
    ) -> tuple[dict[str, Any], str]:
        """Resolve testless strategy for a check plan (ask once + re-plan; best-effort).

        Returns: (final_checks_obj, block_reason). block_reason=="" means OK.
        """

        return resolve_tls_for_checks(
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            hands_last_message=hands_last_message,
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            user_input_batch_id=user_input_batch_id,
            batch_id_after_testless=batch_id_after_testless,
            batch_id_after_tls_claim=batch_id_after_tls_claim,
            tag_after_testless=tag_after_testless,
            tag_after_tls_claim=tag_after_tls_claim,
            notes_prefix=notes_prefix,
            source=source,
            rationale=rationale,
            evidence_window=evidence_window,
            deps=_mk_testless_resolution_deps(),
        )

    def _apply_set_testless_strategy_overlay_update(
        *,
        set_tls: Any,
        decide_event_id: str,
        fallback_batch_id: str,
        default_rationale: str,
        source: str,
    ) -> None:
        """Apply update_project_overlay.set_testless_strategy (canonicalized via Thought DB)."""

        nonlocal overlay

        apply_set_testless_strategy_overlay_update(
            overlay=overlay if isinstance(overlay, dict) else {},
            set_tls=set_tls,
            decide_event_id=decide_event_id,
            fallback_batch_id=fallback_batch_id,
            default_rationale=default_rationale,
            source=source,
            deps=_mk_testless_strategy_flow_deps(),
        )

    def _mine_workflow_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        run_workflow_mining(
            enabled=bool(wf_auto_mine),
            executed_batches=int(executed_batches),
            wf_cfg=wf_cfg if isinstance(wf_cfg, dict) else {},
            seg_evidence=seg_evidence if isinstance(seg_evidence, list) else [],
            base_batch_id=str(base_batch_id or ""),
            source=str(source or ""),
            status=str(status or ""),
            notes=str(notes or ""),
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            thread_id=str(thread_id or ""),
            wf_sigs_counted_in_run=wf_sigs_counted_in_run,
            deps=WorkflowMiningDeps(
                build_decide_context=_build_decide_context,
                suggest_workflow_prompt_builder=suggest_workflow_prompt,
                mind_call=_mind_call,
                evidence_append=evw.append,
                load_workflow_candidates=lambda: load_workflow_candidates(project_paths, warnings=state_warnings),
                write_workflow_candidates=lambda obj: write_workflow_candidates(project_paths, obj),
                flush_state_warnings=_flush_state_warnings,
                write_workflow=wf_store.write,
                new_workflow_id=new_workflow_id,
                enabled_effective_workflows=lambda: [
                    {k: v for k, v in w.items() if k != "_mi_scope"}
                    for w in (wf_registry.enabled_workflows_effective(overlay=overlay) or [])
                    if isinstance(w, dict)
                ],
                sync_hosts=lambda workflows: sync_hosts_from_overlay(
                    overlay=overlay,
                    project_id=project_paths.project_id,
                    workflows=workflows,
                    warnings=state_warnings,
                ),
                now_ts=now_rfc3339,
            ),
        )

    def _mine_preferences_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        run_preference_mining(
            enabled=bool(pref_auto_mine),
            executed_batches=int(executed_batches),
            pref_cfg=pref_cfg if isinstance(pref_cfg, dict) else {},
            seg_evidence=seg_evidence if isinstance(seg_evidence, list) else [],
            base_batch_id=str(base_batch_id or ""),
            source=str(source or ""),
            status=str(status or ""),
            notes=str(notes or ""),
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            thread_id=str(thread_id or ""),
            project_id=project_paths.project_id,
            pref_sigs_counted_in_run=pref_sigs_counted_in_run,
            deps=PreferenceMiningDeps(
                build_decide_context=_build_decide_context,
                mine_preferences_prompt_builder=mine_preferences_prompt,
                mind_call=_mind_call,
                evidence_append=evw.append,
                load_preference_candidates=lambda: load_preference_candidates(project_paths, warnings=state_warnings),
                write_preference_candidates=lambda obj: write_preference_candidates(project_paths, obj),
                flush_state_warnings=_flush_state_warnings,
                existing_signature_map=lambda scope: tdb.existing_signature_map(scope=scope),
                claim_signature_fn=claim_signature,
                preference_signature_fn=preference_signature,
                handle_learn_suggested=_handle_learn_suggested,
                now_ts=now_rfc3339,
            ),
        )

    def _mine_claims_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        """Mine high-signal atomic Claims into Thought DB (checkpoint-only; best-effort)."""

        run_claim_mining(
            enabled=bool(tdb_auto_mine),
            executed_batches=int(executed_batches),
            max_claims=int(tdb_max_claims),
            min_confidence=float(tdb_min_conf),
            seg_evidence=seg_evidence if isinstance(seg_evidence, list) else [],
            base_batch_id=str(base_batch_id or ""),
            source=str(source or ""),
            status=str(status or ""),
            notes=str(notes or ""),
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            thread_id=str(thread_id or ""),
            segment_id=str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else "",
            deps=ClaimMiningDeps(
                build_decide_context=_build_decide_context,
                mine_claims_prompt_builder=mine_claims_prompt,
                mind_call=_mind_call,
                apply_mined_output=tdb.apply_mined_output,
                evidence_append=evw.append,
                now_ts=now_rfc3339,
            ),
        )

    def _materialize_nodes_from_checkpoint(
        *,
        seg_evidence: list[dict[str, Any]],
        snapshot_rec: dict[str, Any] | None,
        base_batch_id: str,
        checkpoint_kind: str,
        status_hint: str,
        planned_next_input: str,
        note: str,
    ) -> None:
        materialize_nodes_from_checkpoint(
            enabled=bool(tdb_enabled) and bool(tdb_auto_nodes),
            seg_evidence=seg_evidence,
            snapshot_rec=snapshot_rec,
            base_batch_id=base_batch_id,
            checkpoint_kind=checkpoint_kind,
            status_hint=status_hint,
            planned_next_input=planned_next_input,
            note=note,
            deps=NodeMaterializeDeps(
                append_node_create=tdb.append_node_create,
                append_edge=tdb.append_edge,
                upsert_memory_items=mem.upsert_items,
                build_index_item=thoughtdb_node_item,
                evidence_append=evw.append,
                now_ts=now_rfc3339,
                truncate=_truncate,
                project_id=project_paths.project_id,
                nodes_path=project_paths.thoughtdb_nodes_path,
                task=task,
                thread_id=str(thread_id or ""),
                segment_id=str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else "",
            ),
        )

    _last_checkpoint_key = ""
    checkpoint_wiring = CheckpointWiringDeps(
        checkpoint_enabled=bool(checkpoint_enabled),
        task=task,
        hands_provider=cur_provider,
        mindspec_base=_mindspec_base_runtime,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        evidence_window=evidence_window,
        thread_id_getter=_cur_thread_id,
        build_decide_context=_build_decide_context,
        checkpoint_decide_prompt_builder=checkpoint_decide_prompt,
        mind_call=_mind_call,
        evidence_append=evw.append,
        mine_workflow_from_segment=_mine_workflow_from_segment,
        mine_preferences_from_segment=_mine_preferences_from_segment,
        mine_claims_from_segment=_mine_claims_from_segment,
        materialize_snapshot=mem.materialize_snapshot,
        materialize_nodes_from_checkpoint=_materialize_nodes_from_checkpoint,
        new_segment_state=_new_segment_state,
        now_ts=now_rfc3339,
        truncate=_truncate,
    )

    def _maybe_checkpoint_and_mine(*, batch_id: str, planned_next_input: str, status_hint: str, note: str) -> None:
        """LLM-judged checkpoint: may mine workflows/preferences and reset segment buffer."""

        nonlocal segment_state, segment_records, _last_checkpoint_key

        res = run_checkpoint_pipeline_wired(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=segment_records if isinstance(segment_records, list) else [],
            last_checkpoint_key=str(_last_checkpoint_key or ""),
            batch_id=batch_id,
            planned_next_input=planned_next_input,
            status_hint=status_hint,
            note=note,
            deps=checkpoint_wiring,
        )

        segment_state = res.segment_state if isinstance(res.segment_state, dict) else {}
        segment_records = res.segment_records if isinstance(res.segment_records, list) else []
        _last_checkpoint_key = str(res.last_checkpoint_key or "")
        if bool(res.persist_segment_state):
            _persist_segment_state()

    def _loop_break_get_checks_input(
        *,
        base_batch_id: str,
        hands_last_message: str,
        thought_db_context: dict[str, Any] | None,
        repo_observation: dict[str, Any] | None,
        existing_check_plan: dict[str, Any] | None,
    ) -> tuple[str, str]:
        """Return checks input text for loop_break run_checks_then_continue (best-effort).

        Returns: (checks_input_text, block_reason). block_reason=="" means OK.
        """
        return run_loop_break_get_checks_input(
            base_batch_id=base_batch_id,
            hands_last_message=hands_last_message,
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            existing_check_plan=existing_check_plan if isinstance(existing_check_plan, dict) else None,
            notes_on_skipped="skipped: mind_circuit_open (plan_min_checks loop_break)",
            notes_on_error="mind_error: plan_min_checks(loop_break) failed; see EvidenceLog kind=mind_error",
            deps=LoopBreakChecksDeps(
                get_check_input=_get_check_input,
                plan_checks_and_record=_plan_checks_and_record,
                resolve_tls_for_checks=_resolve_tls_for_checks,
                empty_check_plan=_empty_check_plan,
            ),
        )

    def _queue_next_input(
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

        nonlocal next_input, status, notes, sent_sigs

        def _loop_guard(**kwargs) -> Any:
            return apply_loop_guard(
                candidate=str(kwargs.get("candidate") or ""),
                hands_last_message=str(kwargs.get("hands_last_message") or ""),
                batch_id=str(kwargs.get("batch_id") or ""),
                reason=str(kwargs.get("reason") or ""),
                sent_sigs=kwargs.get("sent_sigs") if isinstance(kwargs.get("sent_sigs"), list) else [],
                task=task,
                hands_provider=cur_provider,
                mindspec_base=_mindspec_base_runtime(),
                project_overlay=overlay if isinstance(overlay, dict) else {},
                thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
                repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
                check_plan=check_plan if isinstance(check_plan, dict) else {},
                evidence_window=evidence_window,
                thread_id=str(thread_id or ""),
                deps=LoopGuardDeps(
                    loop_sig=_loop_sig,
                    loop_pattern=_loop_pattern,
                    now_ts=now_rfc3339,
                    truncate=_truncate,
                    evidence_append=evw.append,
                    append_segment_record=lambda rec: segment_add_and_persist(
                        segment_add=_segment_add,
                        persist_segment_state=_persist_segment_state,
                        item=rec,
                    ),
                    resolve_ask_when_uncertain=lambda: bool(
                        resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339()).ask_when_uncertain
                    ),
                    loop_break_prompt_builder=loop_break_prompt,
                    mind_call=_mind_call,
                    loop_break_get_checks_input=_loop_break_get_checks_input,
                    read_user_answer=_read_user_answer,
                    append_user_input_record=_append_user_input_record,
                ),
            )

        out = run_queue_next_input(
            nxt=nxt,
            hands_last_message=hands_last_message,
            batch_id=batch_id,
            reason=reason,
            sent_sigs=sent_sigs,
            deps=QueueNextInputDeps(
                loop_guard=_loop_guard,
                checkpoint_before_continue=_maybe_checkpoint_and_mine,
            ),
        )
        sent_sigs = list(out.sent_sigs)
        if not bool(out.queued):
            status = str(out.status or "blocked")
            notes = str(out.notes or "")
            return False
        next_input = str(out.next_input or "")
        status = str(out.status or "not_done")
        notes = str(out.notes or "")
        return True

    def _append_user_input_record(*, batch_id: str, question: str, answer: str) -> dict[str, Any]:
        """Append user input evidence and keep segment/evidence windows in sync."""

        nonlocal evidence_window
        return run_append_user_input_record(
            batch_id=str(batch_id),
            question=question,
            answer=answer,
            evidence_window=evidence_window,
            deps=InteractionRecordDeps(
                evidence_append=evw.append,
                append_window=append_evidence_window,
                append_segment_record=lambda item: segment_add_and_persist(
                    segment_add=_segment_add,
                    persist_segment_state=_persist_segment_state,
                    item=item,
                ),
                now_ts=now_rfc3339,
                thread_id=thread_id,
            ),
        )

    def _append_auto_answer_record(*, batch_id: str, mind_transcript_ref: str, auto_answer: dict[str, Any]) -> dict[str, Any]:
        """Append auto_answer evidence and keep segment/evidence windows in sync."""

        nonlocal evidence_window
        return run_append_auto_answer_record(
            batch_id=str(batch_id),
            mind_transcript_ref=str(mind_transcript_ref or ""),
            auto_answer=auto_answer if isinstance(auto_answer, dict) else {},
            evidence_window=evidence_window,
            deps=InteractionRecordDeps(
                evidence_append=evw.append,
                append_window=append_evidence_window,
                append_segment_record=lambda item: segment_add_and_persist(
                    segment_add=_segment_add,
                    persist_segment_state=_persist_segment_state,
                    item=item,
                ),
                now_ts=now_rfc3339,
                thread_id=thread_id,
            ),
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
        """Fallback when decide_next fails/skips."""

        nonlocal status, notes
        ask_when_uncertain = bool(resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339()).ask_when_uncertain)
        cont, blocked_note = handle_decide_next_missing(
            batch_idx=batch_idx,
            decision_state=str(decision_state or ""),
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            ask_when_uncertain=ask_when_uncertain,
            looks_like_user_question=_looks_like_user_question,
            read_user_answer=_read_user_answer,
            append_user_input_record=_append_user_input_record,
            queue_next_input=_queue_next_input,
        )
        if not cont and blocked_note:
            status = "blocked"
            notes = blocked_note
        return bool(cont)

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
        """Try one auto_answer attempt for an ask_user question.

        Returns:
        - (True/False, q): immediate batch result when an instruction was queued (or queue failed)
        - (None, q): no immediate queue; caller may continue and possibly ask user
        """

        return run_ask_user_auto_answer_attempt(
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
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            recent_evidence=evidence_window,
            deps=AskUserAutoAnswerAttemptDeps(
                empty_auto_answer=_empty_auto_answer,
                build_thought_db_context_obj=lambda hlm, recs: _build_decide_context(
                    hands_last_message=hlm,
                    recent_evidence=recs if isinstance(recs, list) else [],
                ).to_prompt_obj(),
                auto_answer_prompt_builder=auto_answer_to_hands_prompt,
                mind_call=_mind_call,
                append_auto_answer_record=_append_auto_answer_record,
                get_check_input=_get_check_input,
                join_hands_inputs=join_hands_inputs,
                queue_next_input=_queue_next_input,
            ),
        )

    def _ask_user_redecide_with_input(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        answer: str,
    ) -> bool:
        """Re-decide after collecting user input (no extra Hands run before decision)."""

        nonlocal last_decide_next_rec

        cont, decide_rec2 = run_ask_user_redecide_with_input(
            batch_idx=batch_idx,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            workflow_load_effective=wf_registry.load_effective,
            recent_evidence=evidence_window if isinstance(evidence_window, list) else [],
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            answer=answer,
            deps=AskUserRedecideDeps(
                empty_auto_answer=_empty_auto_answer,
                build_decide_context=_build_decide_context,
                summarize_thought_db_context=summarize_thought_db_context,
                decide_next_prompt_builder=decide_next_prompt,
                load_active_workflow=load_active_workflow,
                mind_call=_mind_call,
                log_decide_next=_log_decide_next,
                append_decide_record=lambda rec: segment_add_and_persist(
                    segment_add=_segment_add,
                    persist_segment_state=_persist_segment_state,
                    item=rec,
                ),
                apply_set_testless_strategy_overlay_update=_apply_set_testless_strategy_overlay_update,
                handle_learn_suggested=_handle_learn_suggested,
                get_check_input=_get_check_input,
                join_hands_inputs=join_hands_inputs,
                queue_next_input=_queue_next_input,
                set_status=_set_status,
                set_notes=_set_notes,
            ),
        )
        if isinstance(decide_rec2, dict) and str(decide_rec2.get("event_id") or "").strip():
            last_decide_next_rec = decide_rec2
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
        """Handle decide_next(next_action=ask_user) path with auto-answer retries and re-decide."""

        nonlocal status, notes
        return run_handle_decide_next_ask_user(
            batch_idx=batch_idx,
            task=task,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
            deps=DecideAskUserFlowDeps(
                run_auto_answer_attempt=_ask_user_auto_answer_attempt,
                maybe_cross_project_recall=_maybe_cross_project_recall,
                read_user_answer=_read_user_answer,
                append_user_input_record=_append_user_input_record,
                redecide_with_input=_ask_user_redecide_with_input,
                set_blocked=lambda blocked_note: (
                    _set_status("blocked"),
                    _set_notes(str(blocked_note or "").strip()),
                ),
            ),
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
        """Build decide_next prompt, call Mind, and return decision plus prompt context."""

        return run_query_decide_next(
            batch_idx=batch_idx,
            batch_id=batch_id,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            workflow_load_effective=wf_registry.load_effective,
            recent_evidence=evidence_window if isinstance(evidence_window, list) else [],
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
            deps=DecideNextQueryDeps(
                build_decide_context=_build_decide_context,
                summarize_thought_db_context=summarize_thought_db_context,
                decide_next_prompt_builder=decide_next_prompt,
                load_active_workflow=load_active_workflow,
                mind_call=_mind_call,
            ),
        )

    def _decide_next_record_effects(
        *,
        batch_idx: int,
        decision_obj: dict[str, Any],
        decision_mind_ref: str,
        tdb_ctx_summary: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        """Persist decide_next outputs and apply declared side effects."""

        nonlocal status, notes, last_decide_next_rec

        res = run_record_decide_next_effects(
            batch_idx=batch_idx,
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
            decision_mind_ref=str(decision_mind_ref or ""),
            tdb_ctx_summary=tdb_ctx_summary if isinstance(tdb_ctx_summary, dict) else {},
            deps=DecideRecordEffectsDeps(
                log_decide_next=_log_decide_next,
                segment_add=_segment_add,
                persist_segment_state=_persist_segment_state,
                apply_set_testless_strategy_overlay_update=_apply_set_testless_strategy_overlay_update,
                handle_learn_suggested=_handle_learn_suggested,
                emit_prefixed=_emit_prefixed,
            ),
        )

        if isinstance(res.decide_rec, dict) and str(res.decide_rec.get("event_id") or "").strip():
            last_decide_next_rec = res.decide_rec
        status = str(res.status or "not_done")
        notes = str(res.notes or "")
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
        """Route and apply next_action from decide_next."""

        nonlocal status, notes
        cont, blocked_note = route_decide_next_action(
            batch_idx=batch_idx,
            next_action=str(next_action or ""),
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
            handle_ask_user=_handle_decide_next_ask_user,
            queue_next_input=_queue_next_input,
        )
        if not cont and blocked_note:
            status = "blocked"
            notes = blocked_note
        return bool(cont)

    def _phase_decide_next(
        *,
        batch_idx: int,
        batch_id: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        auto_answer_obj: dict[str, Any],
    ) -> bool:
        return run_decide_next_phase(
            batch_idx=batch_idx,
            batch_id=batch_id,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
            deps=DecidePhaseDeps(
                query=_decide_next_query,
                handle_missing=_handle_decide_next_missing,
                record_effects=_decide_next_record_effects,
                route_action=_decide_next_route_action,
            ),
        )

    def _predecide_run_hands(*, ctx: BatchExecutionContext) -> Any:
        """Execute Hands for one batch and persist session/input records."""

        nonlocal thread_id, executed_batches

        result, st = run_hands_batch(
            ctx=ctx,
            state=RunState(thread_id=thread_id, executed_batches=executed_batches),
            deps=HandsFlowDeps(
                run_deps=RunDeps(
                    emit_prefixed=_emit_prefixed,
                    now_ts=now_rfc3339,
                    evidence_append=evw.append,
                ),
                project_root=project_path,
                transcripts_dir=project_paths.transcripts_dir,
                cur_provider=cur_provider,
                no_mi_prompt=bool(no_mi_prompt),
                interrupt_cfg=interrupt_cfg,
                overlay=overlay,
                hands_exec=hands_exec,
                hands_resume=hands_resume,
                write_overlay=lambda ov: write_project_overlay(home_dir=home, project_root=project_path, overlay=ov),
            ),
        )
        thread_id = st.thread_id
        executed_batches = int(st.executed_batches or 0)
        return result

    def _predecide_retry_auto_answer_after_recall(
        *,
        batch_idx: int,
        question: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """Retry auto_answer after conservative cross-project recall."""
        return run_predecide_retry_auto_answer_after_recall(
            batch_idx=batch_idx,
            question=question,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            recent_evidence=evidence_window,
            deps=PredecideRetryAutoAnswerDeps(
                empty_auto_answer=_empty_auto_answer,
                maybe_cross_project_recall=_maybe_cross_project_recall,
                auto_answer_prompt_builder=auto_answer_to_hands_prompt,
                mind_call=_mind_call,
                append_auto_answer_record=_append_auto_answer_record,
            ),
        )

    def _predecide_try_queue_answer_with_checks(
        *,
        batch_idx: int,
        batch_id: str,
        queue_reason: str,
        answer_text: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> bool | None:
        """Queue answer + checks when either side has content."""
        return run_predecide_try_queue_answer_with_checks(
            batch_id=batch_id,
            queue_reason=queue_reason,
            answer_text=answer_text,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=PredecideQueueWithChecksDeps(
                get_check_input=_get_check_input,
                join_hands_inputs=join_hands_inputs,
                queue_next_input=_queue_next_input,
            ),
        )

    def _predecide_prompt_user_then_queue(
        *,
        batch_idx: int,
        question: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> bool:
        """Ask the user and queue answer (+ checks)."""
        return run_predecide_prompt_user_then_queue(
            batch_idx=batch_idx,
            question=question,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=PredecidePromptUserDeps(
                read_user_answer=_read_user_answer,
                append_user_input_record=_append_user_input_record,
                set_blocked=lambda blocked_note: (
                    _set_status("blocked"),
                    _set_notes(str(blocked_note or "").strip()),
                ),
                try_queue_answer_with_checks=lambda **kwargs: _predecide_try_queue_answer_with_checks(
                    batch_idx=batch_idx,
                    batch_id=str(kwargs.get("batch_id") or ""),
                    queue_reason=str(kwargs.get("queue_reason") or ""),
                    answer_text=str(kwargs.get("answer_text") or ""),
                    hands_last=str(kwargs.get("hands_last") or ""),
                    repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                    checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                    tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                ),
            ),
        )

    def _predecide_handle_auto_answer_needs_user(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
        checks_obj: dict[str, Any],
        auto_answer_obj: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Handle pre-decide branch where initial auto_answer requests user input."""
        return run_predecide_handle_auto_answer_needs_user(
            batch_idx=batch_idx,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
            deps=PredecideNeedsUserDeps(
                retry_auto_answer_after_recall=lambda **kwargs: _predecide_retry_auto_answer_after_recall(
                    batch_idx=batch_idx,
                    question=str(kwargs.get("question") or ""),
                    repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                    checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                    tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                ),
                try_queue_answer_with_checks=lambda **kwargs: _predecide_try_queue_answer_with_checks(
                    batch_idx=batch_idx,
                    batch_id=str(kwargs.get("batch_id") or ""),
                    queue_reason=str(kwargs.get("queue_reason") or ""),
                    answer_text=str(kwargs.get("answer_text") or ""),
                    hands_last=str(kwargs.get("hands_last") or ""),
                    repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                    checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                    tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                ),
                prompt_user_then_queue=lambda **kwargs: _predecide_prompt_user_then_queue(
                    batch_idx=batch_idx,
                    question=str(kwargs.get("question") or ""),
                    hands_last=str(kwargs.get("hands_last") or ""),
                    repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                    checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                    tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                ),
            ),
        )

    def _predecide_apply_preactions(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
        checks_obj: dict[str, Any],
        auto_answer_obj: dict[str, Any],
    ) -> tuple[bool | None, dict[str, Any]]:
        """Apply deterministic pre-action arbitration before decide_next."""

        nonlocal status, notes

        if isinstance(auto_answer_obj, dict) and bool(auto_answer_obj.get("needs_user_input", False)):
            handled, checks_out = _predecide_handle_auto_answer_needs_user(
                batch_idx=batch_idx,
                hands_last=hands_last,
                repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
                tdb_ctx_batch_obj=tdb_ctx_batch_obj,
                checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
                auto_answer_obj=auto_answer_obj,
            )
            return handled, checks_out

        checks_obj, block_reason = _resolve_tls_for_checks(
            checks_obj=checks_obj if isinstance(checks_obj, dict) else _empty_check_plan(),
            hands_last_message=hands_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            user_input_batch_id=f"b{batch_idx}",
            batch_id_after_testless=f"b{batch_idx}.after_testless",
            batch_id_after_tls_claim=f"b{batch_idx}.after_tls_claim",
            tag_after_testless=f"checks_after_tls_b{batch_idx}",
            tag_after_tls_claim=f"checks_after_tls_claim_b{batch_idx}",
            notes_prefix="",
            source="user_input:testless_strategy",
            rationale="user provided testless verification strategy",
        )
        if block_reason:
            status = "blocked"
            notes = block_reason
            return False, checks_obj

        answer_text = ""
        if isinstance(auto_answer_obj, dict) and bool(auto_answer_obj.get("should_answer", False)):
            answer_text = str(auto_answer_obj.get("hands_answer_input") or "").strip()
        queued = _predecide_try_queue_answer_with_checks(
            batch_idx=batch_idx,
            batch_id=f"b{batch_idx}",
            queue_reason="sent auto-answer/checks to Hands",
            answer_text=answer_text,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj,
        )
        if isinstance(queued, bool):
            return queued, checks_obj
        return None, checks_obj

    def _predecide_extract_evidence_and_context(
        *,
        batch_idx: int,
        batch_id: str,
        ctx: BatchExecutionContext,
        result: Any,
        repo_obs: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
        """Run extract_evidence and build Thought DB context for this batch."""

        nonlocal last_evidence_rec, evidence_window

        summary = _batch_summary(result)
        extract_prompt = extract_evidence_prompt(
            task=task,
            hands_provider=cur_provider,
            light_injection=ctx.light_injection,
            batch_input=ctx.batch_input,
            hands_batch_summary=summary,
            repo_observation=repo_obs,
        )
        evidence_obj, evidence_mind_ref, evidence_state = _mind_call(
            schema_filename="extract_evidence.json",
            prompt=extract_prompt,
            tag=f"extract_b{batch_idx}",
            batch_id=batch_id,
        )
        if evidence_obj is None:
            if evidence_state == "skipped":
                evidence_obj = _empty_evidence_obj(note="mind_circuit_open: extract_evidence skipped")
            else:
                evidence_obj = _empty_evidence_obj(note="mind_error: extract_evidence failed; see EvidenceLog kind=mind_error")

        counts = extract_evidence_counts(evidence_obj if isinstance(evidence_obj, dict) else None)
        _emit_prefixed(
            "[mi]",
            "extract_evidence "
            + f"state={str(evidence_state or '')} "
            + f"facts={counts['facts']} actions={counts['actions']} "
            + f"results={counts['results']} unknowns={counts['unknowns']} risk_signals={counts['risk_signals']}",
        )
        evidence_rec = append_evidence_with_tracking(
            batch_id=batch_id,
            hands_transcript_ref=str(ctx.hands_transcript),
            mind_transcript_ref=evidence_mind_ref,
            mi_input=ctx.batch_input,
            transcript_observation=summary.get("transcript_observation") or {},
            repo_observation=repo_obs,
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            evidence_window=evidence_window,
            deps=EvidenceAppendDeps(
                evidence_append=evw.append,
                append_window=append_evidence_window,
                segment_add=lambda item: segment_add_and_persist(
                    segment_add=_segment_add,
                    persist_segment_state=_persist_segment_state,
                    item=item if isinstance(item, dict) else {},
                ),
                now_ts=now_rfc3339,
                thread_id=thread_id,
            ),
        )
        last_evidence_rec = evidence_rec

        hands_last = result.last_agent_message()
        tdb_ctx_batch = _build_decide_context(hands_last_message=hands_last, recent_evidence=evidence_window)
        tdb_ctx_batch_obj = tdb_ctx_batch.to_prompt_obj()
        return (
            summary if isinstance(summary, dict) else {},
            evidence_obj if isinstance(evidence_obj, dict) else {},
            str(hands_last or ""),
            tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        )

    def _predecide_apply_workflow_progress(
        *,
        batch_idx: int,
        batch_id: str,
        summary: dict[str, Any],
        evidence_obj: dict[str, Any],
        repo_obs: dict[str, Any],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
        ctx: BatchExecutionContext,
    ) -> None:
        """Update workflow cursor/state using workflow_progress output (best-effort)."""

        active_wf = load_active_workflow(workflow_run=workflow_run, load_effective=wf_registry.load_effective)
        if not (isinstance(active_wf, dict) and active_wf):
            return

        latest_evidence = build_workflow_progress_latest_evidence(
            batch_id=batch_id,
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        )
        wf_prog_obj, wf_prog_ref, wf_prog_state = query_workflow_progress(
            batch_idx=batch_idx,
            batch_id=batch_id,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            active_workflow=active_wf,
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            latest_evidence=latest_evidence,
            last_batch_input=ctx.batch_input,
            hands_last_message=hands_last,
            thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=WorkflowProgressQueryDeps(
                workflow_progress_prompt_builder=workflow_progress_prompt,
                mind_call=_mind_call,
            ),
        )
        append_workflow_progress_event(
            batch_id=batch_id,
            thread_id=thread_id,
            active_workflow=active_wf,
            wf_prog_obj=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
            wf_prog_ref=wf_prog_ref,
            wf_prog_state=wf_prog_state,
            evidence_append=evw.append,
            now_ts=now_rfc3339,
        )

        def _persist_workflow_overlay() -> None:
            overlay["workflow_run"] = workflow_run
            write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)

        apply_workflow_progress_and_persist(
            batch_id=batch_id,
            thread_id=str(thread_id or ""),
            active_workflow=active_wf,
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            wf_prog_obj=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
            apply_workflow_progress_output_fn=apply_workflow_progress_output,
            persist_overlay=_persist_workflow_overlay,
            now_ts=now_rfc3339,
        )

    def _predecide_detect_risk_signals(*, result: Any, ctx: BatchExecutionContext) -> list[str]:
        """Detect risk signals from structured events, then transcript fallback when needed."""

        risk_signals = _detect_risk_signals(result)
        if not risk_signals and not (isinstance(getattr(result, "events", None), list) and result.events):
            risk_signals = _detect_risk_signals_from_transcript(ctx.hands_transcript)
        return [str(x) for x in risk_signals if str(x).strip()]

    def _predecide_query_risk_judge(
        *,
        batch_idx: int,
        batch_id: str,
        risk_signals: list[str],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """Run recall + risk_judge and normalize fallback output."""
        return query_risk_judge(
            batch_idx=batch_idx,
            batch_id=batch_id,
            risk_signals=risk_signals,
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            maybe_cross_project_recall=_maybe_cross_project_recall,
            risk_judge_prompt_builder=risk_judge_prompt,
            mind_call=_mind_call,
            build_risk_fallback=build_risk_fallback,
        )

    def _predecide_record_risk_event(
        *,
        batch_idx: int,
        risk_signals: list[str],
        risk_obj: dict[str, Any],
        risk_mind_ref: str,
    ) -> dict[str, Any]:
        """Persist risk event to EvidenceLog + segment + evidence window."""

        return append_risk_event_with_tracking(
            batch_idx=batch_idx,
            risk_signals=risk_signals,
            risk_obj=risk_obj if isinstance(risk_obj, dict) else {},
            risk_mind_ref=risk_mind_ref,
            evidence_window=evidence_window,
            deps=RiskEventAppendDeps(
                evidence_append=evw.append,
                append_window=append_evidence_window,
                segment_add=lambda item: segment_add_and_persist(
                    segment_add=_segment_add,
                    persist_segment_state=_persist_segment_state,
                    item=item if isinstance(item, dict) else {},
                ),
                now_ts=now_rfc3339,
                thread_id=thread_id,
            ),
        )

    def _predecide_apply_risk_learn_suggested(
        *,
        batch_idx: int,
        risk_obj: dict[str, Any],
        risk_mind_ref: str,
        risk_event_id: str,
    ) -> None:
        """Apply learn_suggested emitted by risk_judge."""

        _handle_learn_suggested(
            learn_suggested=(risk_obj if isinstance(risk_obj, dict) else {}).get("learn_suggested"),
            batch_id=f"b{batch_idx}",
            source="risk_judge",
            mind_transcript_ref=risk_mind_ref,
            source_event_ids=[str(risk_event_id or "").strip()],
        )

    def _predecide_maybe_prompt_risk_continue(*, risk_obj: dict[str, Any]) -> bool | None:
        """Apply runtime violation policy; return False when user blocks run."""

        nonlocal status, notes

        vr = runtime_cfg.get("violation_response") if isinstance(runtime_cfg.get("violation_response"), dict) else {}
        out = maybe_prompt_risk_continue(
            risk_obj=risk_obj if isinstance(risk_obj, dict) else {},
            should_prompt_risk_user=should_prompt_risk_user,
            violation_response_cfg=vr if isinstance(vr, dict) else {},
            read_user_answer=_read_user_answer,
        )
        if out is False:
            status = "blocked"
            notes = "stopped after risk event"
            return False
        return out

    def _predecide_judge_and_handle_risk(
        *,
        batch_idx: int,
        batch_id: str,
        risk_signals: list[str],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> bool | None:
        """Run risk_judge, record evidence, and enforce runtime violation policy."""
        return run_risk_predecide(
            batch_idx=batch_idx,
            batch_id=batch_id,
            risk_signals=risk_signals,
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=RiskPredecideDeps(
                query_risk=_predecide_query_risk_judge,
                record_risk=_predecide_record_risk_event,
                apply_learn_suggested=_predecide_apply_risk_learn_suggested,
                maybe_prompt_continue=_predecide_maybe_prompt_risk_continue,
            ),
        )

    def _predecide_apply_workflow_and_risk(
        *,
        batch_idx: int,
        batch_id: str,
        result: Any,
        summary: dict[str, Any],
        evidence_obj: dict[str, Any],
        repo_obs: dict[str, Any],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
        ctx: BatchExecutionContext,
    ) -> bool | None:
        """Apply workflow progress and risk handling before checks/auto-answer."""

        return run_workflow_and_risk_phase(
            batch_idx=batch_idx,
            batch_id=batch_id,
            result=result,
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            ctx=ctx,
            deps=WorkflowRiskPhaseDeps(
                apply_workflow_progress=_predecide_apply_workflow_progress,
                detect_risk_signals=_predecide_detect_risk_signals,
                judge_and_handle_risk=_predecide_judge_and_handle_risk,
            ),
        )

    def _predecide_plan_checks(
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

        should_plan_checks = _should_plan_checks(
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            hands_last_message=hands_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        )
        checks_obj, _, _ = _plan_checks_and_record(
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
            _emit_prefixed("[mi]", compose_check_plan_log(checks_obj))
            return checks_obj
        return _empty_check_plan()

    def _auto_answer_query_and_normalize(
        *,
        batch_idx: int,
        batch_id: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> tuple[dict[str, Any], str, str]:
        """Query auto_answer_to_hands and normalize fallback object."""

        return query_auto_answer_to_hands(
            batch_idx=batch_idx,
            batch_id=batch_id,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            recent_evidence=evidence_window,
            hands_last_message=hands_last,
            deps=AutoAnswerQueryDeps(
                auto_answer_prompt_builder=auto_answer_to_hands_prompt,
                mind_call=_mind_call,
                empty_auto_answer=_empty_auto_answer,
            ),
        )

    def _predecide_maybe_auto_answer(
        *,
        batch_idx: int,
        batch_id: str,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> dict[str, Any]:
        """Auto-answer Hands only when last message looks like a direct question."""

        if not _looks_like_user_question(hands_last):
            return _empty_auto_answer()

        auto_answer_obj, auto_answer_mind_ref, aa_state = _auto_answer_query_and_normalize(
            batch_idx=batch_idx,
            batch_id=batch_id,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        )
        _emit_prefixed(
            "[mi]",
            compose_auto_answer_log(state=str(aa_state or ""), auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {}),
        )
        _append_auto_answer_record(
            batch_id=f"b{batch_idx}",
            mind_transcript_ref=auto_answer_mind_ref,
            auto_answer=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
        )
        return auto_answer_obj if isinstance(auto_answer_obj, dict) else _empty_auto_answer()

    def _predecide_plan_checks_and_auto_answer(
        *,
        batch_idx: int,
        batch_id: str,
        summary: dict[str, Any],
        evidence_obj: dict[str, Any],
        repo_obs: dict[str, Any],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Plan checks and optionally auto-answer Hands questions."""

        checks_obj, auto_answer_obj = run_plan_checks_and_auto_answer(
            batch_idx=batch_idx,
            batch_id=batch_id,
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=PlanChecksAutoAnswerDeps(
                plan_checks=_predecide_plan_checks,
                maybe_auto_answer=_predecide_maybe_auto_answer,
            ),
        )
        return (
            checks_obj if isinstance(checks_obj, dict) else _empty_check_plan(),
            auto_answer_obj if isinstance(auto_answer_obj, dict) else _empty_auto_answer(),
        )

    def _dict_or_empty(obj: Any) -> dict[str, Any]:
        return obj if isinstance(obj, dict) else {}

    def _build_batch_execution_context(*, batch_idx: int) -> BatchExecutionContext:
        return build_batch_execution_context(
            batch_idx=batch_idx,
            transcripts_dir=project_paths.transcripts_dir,
            next_input=next_input,
            thread_id=thread_id,
            hands_resume=hands_resume,
            resumed_from_overlay=bool(resumed_from_overlay),
            now_ts=now_rfc3339,
            build_light_injection_for_ts=lambda as_of_ts: build_light_injection(tdb=tdb, as_of_ts=as_of_ts),
        )

    last_evidence_rec: dict[str, Any] | None = None
    last_decide_next_rec: dict[str, Any] | None = None

    executed_batches = 0
    last_batch_id = ""
    def _run_predecide_via_service(req: BatchRunRequest) -> bool | PreactionDecision:
        nonlocal last_batch_id
        out = run_batch_predecide(
            batch_idx=int(req.batch_idx),
            deps=BatchPredecideDeps(
                build_context=_build_batch_execution_context,
                run_hands=_predecide_run_hands,
                observe_repo=lambda: _observe_repo(project_path),
                dict_or_empty=_dict_or_empty,
                extract_deps=ExtractEvidenceDeps(extract_context=_predecide_extract_evidence_and_context),
                workflow_risk_deps=WorkflowRiskPhaseDeps(
                    apply_workflow_progress=_predecide_apply_workflow_progress,
                    detect_risk_signals=_predecide_detect_risk_signals,
                    judge_and_handle_risk=_predecide_judge_and_handle_risk,
                ),
                checks_deps=PlanChecksAutoAnswerDeps(
                    plan_checks=_predecide_plan_checks,
                    maybe_auto_answer=_predecide_maybe_auto_answer,
                ),
                preaction_deps=PreactionPhaseDeps(
                    apply_preactions=_predecide_apply_preactions,
                    empty_auto_answer=_empty_auto_answer,
                ),
            ),
        )
        last_batch_id = str(out.batch_id or f"b{int(req.batch_idx)}")
        return out.out

    def _run_decide_via_service(req: BatchRunRequest, preaction: PreactionDecision) -> bool:
        return _phase_decide_next(
            batch_idx=int(req.batch_idx),
            batch_id=str(req.batch_id or f"b{int(req.batch_idx)}"),
            hands_last=str(preaction.hands_last or ""),
            repo_obs=preaction.repo_obs if isinstance(preaction.repo_obs, dict) else {},
            checks_obj=preaction.checks_obj if isinstance(preaction.checks_obj, dict) else {},
            auto_answer_obj=preaction.auto_answer_obj if isinstance(preaction.auto_answer_obj, dict) else _empty_auto_answer(),
        )

    def _run_learn_update() -> None:
        maybe_run_learn_update_on_run_end(
            runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
            executed_batches=int(executed_batches),
            last_batch_id=str(last_batch_id or ""),
            learn_suggested_records_this_run=learn_suggested_records_this_run,
            tdb=tdb,
            evw=evw,
            mind_call=_mind_call,
            emit_prefixed=_emit_prefixed,
            truncate=_truncate,
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay if isinstance(overlay, dict) else {},
            status=str(status or ""),
            notes=str(notes or ""),
            thread_id=(thread_id or ""),
        )

    def _run_why_trace() -> None:
        maybe_run_why_trace_on_run_end(
            enabled=bool(auto_why_on_end),
            executed_batches=int(executed_batches),
            last_batch_id=str(last_batch_id or ""),
            last_decide_next_rec=last_decide_next_rec if isinstance(last_decide_next_rec, dict) else None,
            last_evidence_rec=last_evidence_rec if isinstance(last_evidence_rec, dict) else None,
            tdb=tdb,
            mem_service=mem.service,
            project_paths=project_paths,
            why_top_k=int(why_top_k),
            why_write_edges=bool(why_write_edges),
            why_min_write_conf=float(why_min_write_conf),
            mind_call=_mind_call,
            evw=evw,
            thread_id=(thread_id or ""),
        )

    def _set_status(value: str) -> None:
        nonlocal status
        status = str(value or "")

    def _set_notes(value: str) -> None:
        nonlocal notes
        notes = str(value or "")

    def _set_last_batch_id(value: str) -> None:
        nonlocal last_batch_id
        last_batch_id = str(value or "")

    def _run_checkpoint_request(request: Any) -> None:
        _maybe_checkpoint_and_mine(
            batch_id=str(request.batch_id or ""),
            planned_next_input=str(request.planned_next_input or ""),
            status_hint=str(request.status_hint or ""),
            note=str(request.note or ""),
        )

    orchestrator = RunLoopOrchestrator(
        deps=RunLoopOrchestratorDeps(
            max_batches=int(max_batches),
            run_predecide_phase=_run_predecide_via_service,
            run_decide_phase=_run_decide_via_service,
            next_input_getter=lambda: str(next_input or ""),
            thread_id_getter=lambda: str(thread_id or ""),
            status_getter=lambda: str(status or ""),
            status_setter=_set_status,
            notes_getter=lambda: str(notes or ""),
            notes_setter=_set_notes,
            last_batch_id_getter=lambda: str(last_batch_id or ""),
            last_batch_id_setter=_set_last_batch_id,
            executed_batches_getter=lambda: int(executed_batches),
            checkpoint_enabled=bool(checkpoint_enabled),
            checkpoint_runner=_run_checkpoint_request,
            learn_runner=_run_learn_update,
            why_runner=_run_why_trace,
            snapshot_flusher=tdb.flush_snapshots_best_effort,
            state_warning_flusher=_flush_state_warnings,
        )
    )
    orchestrator.run()

    return AutopilotResult(
        status=status,
        thread_id=thread_id or "unknown",
        project_dir=run_session.project_paths.project_dir,
        evidence_log_path=run_session.project_paths.evidence_log_path,
        transcripts_dir=run_session.project_paths.transcripts_dir,
        batches=executed_batches,
        notes=notes,
    )
