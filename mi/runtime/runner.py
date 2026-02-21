from __future__ import annotations

import json
import sys
import hashlib
import secrets
import time
from pathlib import Path
from typing import Any

from ..providers.codex_runner import InterruptConfig, run_codex_exec, run_codex_resume
from ..providers.llm import MiLlm
from ..providers.mind_errors import MindCallError
from ..core.config import load_config
from ..core.paths import GlobalPaths, ProjectPaths, default_home_dir
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
    match_workflow_for_task,
    maybe_run_learn_update_on_run_end,
    maybe_run_why_trace_on_run_end,
    summarize_thought_db_context,
    workflow_step_ids,
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
    RunSession,
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
from .autopilot.risk_predecide import (
    RiskPredecideDeps,
    maybe_prompt_risk_continue,
    query_risk_judge,
    run_risk_predecide,
)
from .autopilot.segment_state import (
    add_segment_record,
    clear_segment_state,
    load_segment_state,
    new_segment_state,
    persist_segment_state,
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
from ..core.storage import append_jsonl, ensure_dir, now_rfc3339, read_json_best_effort, write_json_atomic
from ..core.redact import redact_text
from ..workflows import (
    WorkflowStore,
    GlobalWorkflowStore,
    WorkflowRegistry,
    load_workflow_candidates,
    write_workflow_candidates,
    new_workflow_id,
    render_workflow_markdown,
)
from ..workflows.preferences import load_preference_candidates, write_preference_candidates, preference_signature
from ..workflows.hosts import sync_hosts_from_overlay
from ..memory.facade import MemoryFacade
from ..memory.ingest import thoughtdb_node_item
from .evidence import EvidenceWriter, new_run_id
from .injection import build_light_injection
from ..thoughtdb import ThoughtDbStore, claim_signature
from ..thoughtdb.app_service import ThoughtDbApplicationService
from ..thoughtdb.operational_defaults import ensure_operational_defaults_claims_current, resolve_operational_defaults
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
    project_path = Path(project_root).resolve()
    home = Path(home_dir).expanduser().resolve() if home_dir else default_home_dir()
    cfg = load_config(home)
    runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    state_warnings: list[dict[str, Any]] = []

    def _mindspec_base_runtime() -> dict[str, Any]:
        """Runtime knobs context for Mind prompts.

        Historical name: "MindSpec base". Canonical values/preferences and operational defaults
        are in Thought DB Claims; this object is only runtime knobs (budgets/feature switches).
        """

        return runtime_cfg if isinstance(runtime_cfg, dict) else {}

    # Cross-run Hands session persistence is stored in ProjectOverlay but only used when explicitly enabled.
    overlay: dict[str, Any]
    hands_state: dict[str, Any]
    workflow_run: dict[str, Any]

    def _refresh_overlay_refs() -> None:
        nonlocal overlay, hands_state, workflow_run
        overlay = load_project_overlay(home_dir=home, project_root=project_path, warnings=state_warnings)
        if not isinstance(overlay, dict):
            overlay = {}
        hs = overlay.get("hands_state")
        if isinstance(hs, dict):
            hands_state = hs
        else:
            hands_state = {}
            overlay["hands_state"] = hands_state
        wr = overlay.get("workflow_run")
        if isinstance(wr, dict):
            workflow_run = wr
        else:
            workflow_run = {}
            overlay["workflow_run"] = workflow_run

    _refresh_overlay_refs()

    cur_provider = (hands_provider or str(hands_state.get("provider") or "")).strip()
    if reset_hands:
        hands_state["provider"] = cur_provider
        hands_state["thread_id"] = ""
        hands_state["updated_ts"] = now_rfc3339()
        # Reset any best-effort workflow cursor that may be tied to the previous Hands thread.
        overlay["workflow_run"] = {}
        write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
        _refresh_overlay_refs()

    project_paths = ProjectPaths(home_dir=home, project_root=project_path)
    ensure_dir(project_paths.project_dir)
    ensure_dir(project_paths.transcripts_dir)

    wf_store = WorkflowStore(project_paths)
    wf_global_store = GlobalWorkflowStore(GlobalPaths(home_dir=home))
    wf_registry = WorkflowRegistry(project_store=wf_store, global_store=wf_global_store)
    mem = MemoryFacade(home_dir=home, project_paths=project_paths, runtime_cfg=runtime_cfg)
    mem.ensure_structured_ingested()
    tdb = ThoughtDbStore(home_dir=home, project_paths=project_paths)
    tdb_app = ThoughtDbApplicationService(tdb=tdb, project_paths=project_paths, mem=mem.service)
    evw = EvidenceWriter(path=project_paths.evidence_log_path, run_id=new_run_id("run"))

    if llm is None:
        llm = MiLlm(project_root=project_path, transcripts_dir=project_paths.transcripts_dir)
    if hands_exec is None:
        hands_exec = run_codex_exec
    if hands_resume is _DEFAULT:
        hands_resume = run_codex_resume

    live_enabled = bool(live) and (not bool(quiet))

    def _emit_prefixed(prefix: str, text: str) -> None:
        if not live_enabled:
            return
        s = str(text or "")
        if redact:
            s = redact_text(s)
        lines = s.splitlines() if s else [""]
        for line in lines:
            if line:
                print(f"{prefix} {line}", flush=True)
            else:
                print(prefix, flush=True)

    run_session = RunSession(
        home=home,
        project_path=project_path,
        project_paths=project_paths,
        runtime_cfg=(runtime_cfg if isinstance(runtime_cfg, dict) else {}),
        llm=llm,
        hands_exec=hands_exec,
        hands_resume=hands_resume,
        evw=evw,
        tdb=tdb,
        mem=mem,
        wf_registry=wf_registry,
        emit=_emit_prefixed,
        read_user_answer=_read_user_answer,
        now_ts=now_rfc3339,
    )

    evidence_window: list[dict[str, Any]] = []
    thread_id: str | None = None
    resumed_from_overlay = False
    if continue_hands and not reset_hands and hands_resume is not None:
        prev_tid = str(hands_state.get("thread_id") or "").strip()
        prev_provider = str(hands_state.get("provider") or "").strip()
        if prev_tid and prev_tid != "unknown" and (not cur_provider or not prev_provider or prev_provider == cur_provider):
            thread_id = prev_tid
            resumed_from_overlay = True

    # Default: do not carry an "active" workflow cursor across runs unless we are explicitly continuing the same Hands session.
    if bool(workflow_run.get("active", False)) and not bool(resumed_from_overlay):
        workflow_run.clear()
        overlay["workflow_run"] = workflow_run
        write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
    next_input: str = task

    status = "not_done"
    notes = ""

    def _build_decide_context(*, hands_last_message: str, recent_evidence: list[dict[str, Any]]) -> Any:
        return tdb_app.build_decide_context(
            as_of_ts=now_rfc3339(),
            task=task,
            hands_last_message=hands_last_message,
            recent_evidence=recent_evidence,
        )

    # Workflow trigger routing (effective): if an enabled workflow (project or global) matches the task,
    # inject it into the very first batch input (lightweight; no step slicing).
    matched = match_workflow_for_task(task_text=task, workflows=wf_registry.enabled_workflows_effective(overlay=overlay))
    if matched:
        wid = str(matched.get("id") or "").strip()
        name = str(matched.get("name") or "").strip()
        trig = matched.get("trigger") if isinstance(matched.get("trigger"), dict) else {}
        pat = str(trig.get("pattern") or "").strip()
        # Best-effort workflow cursor: internal only. It does NOT impose step-by-step reporting.
        # The cursor is used to provide next-step context to Mind prompts.
        step_ids = workflow_step_ids(matched)
        workflow_run.clear()
        workflow_run.update(
            {
                "version": "v1",
                "active": True,
                "workflow_id": wid,
                "workflow_name": name,
                "thread_id": str(thread_id or hands_state.get("thread_id") or ""),
                "started_ts": now_rfc3339(),
                "updated_ts": now_rfc3339(),
                "completed_step_ids": [],
                "next_step_id": step_ids[0] if step_ids else "",
                "last_batch_id": "b0.workflow_trigger",
                "last_confidence": 0.0,
                "last_notes": f"triggered: task_contains pattern={pat}",
            }
        )
        overlay["workflow_run"] = workflow_run
        write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
        injected = "\n".join(
            [
                "[MI Workflow Triggered]",
                "A reusable workflow matches this task.",
                "- Use it as guidance; you do NOT need to report step-by-step.",
                "- If network/install/push/publish is not clearly safe per values, pause and ask.",
                "",
                render_workflow_markdown(matched),
                "",
                "User task:",
                task.strip(),
            ]
        ).strip()
        next_input = injected
        wf_trig = evw.append(
            {
                "kind": "workflow_trigger",
                "batch_id": "b0.workflow_trigger",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "workflow_id": wid,
                "workflow_name": name,
                "trigger_mode": str(trig.get("mode") or ""),
                "trigger_pattern": pat,
            }
        )
        evidence_window.append(
            {
                "kind": "workflow_trigger",
                "batch_id": "b0.workflow_trigger",
                "event_id": wf_trig.get("event_id"),
                "workflow_id": wid,
                "workflow_name": name,
                "trigger_mode": str(trig.get("mode") or ""),
                "trigger_pattern": pat,
            }
        )

    # Checkpoint/segment mining settings (V1):
    # - Segments are internal; they do NOT impose a step protocol on Hands.
    # - Checkpoints decide when to mine workflows/preferences and reset the segment buffer.
    wf_cfg = runtime_cfg.get("workflows") if isinstance(runtime_cfg.get("workflows"), dict) else {}
    wf_auto_mine = bool(wf_cfg.get("auto_mine", True))
    pref_cfg = runtime_cfg.get("preference_mining") if isinstance(runtime_cfg.get("preference_mining"), dict) else {}
    pref_auto_mine = bool(pref_cfg.get("auto_mine", True))
    tdb_cfg = runtime_cfg.get("thought_db") if isinstance(runtime_cfg.get("thought_db"), dict) else {}
    tdb_enabled = bool(tdb_cfg.get("enabled", True))
    tdb_auto_mine = bool(tdb_cfg.get("auto_mine", True)) and bool(tdb_enabled)
    # Deterministic node materialization does not add mind calls; keep it separately controllable.
    tdb_auto_nodes = bool(tdb_cfg.get("auto_materialize_nodes", True)) and bool(tdb_enabled)
    try:
        tdb_min_conf = float(tdb_cfg.get("min_confidence", 0.9) or 0.9)
    except Exception:
        tdb_min_conf = 0.9
    tdb_min_conf = max(0.0, min(1.0, tdb_min_conf))
    try:
        tdb_max_claims = int(tdb_cfg.get("max_claims_per_checkpoint", 6) or 6)
    except Exception:
        tdb_max_claims = 6
    tdb_max_claims = max(0, min(20, tdb_max_claims))

    # Optional: automatic run-end WhyTrace (opt-in; one call per `mi run`).
    why_cfg = tdb_cfg.get("why_trace") if isinstance(tdb_cfg.get("why_trace"), dict) else {}
    auto_why_on_end = (bool(why_cfg.get("auto_on_run_end", False)) or bool(why_trace_on_run_end)) and bool(tdb_enabled)
    try:
        why_top_k = int(why_cfg.get("top_k", 12) or 12)
    except Exception:
        why_top_k = 12
    why_top_k = max(1, min(40, why_top_k))
    try:
        why_min_write_conf = float(why_cfg.get("min_write_confidence", 0.7) or 0.7)
    except Exception:
        why_min_write_conf = 0.7
    why_min_write_conf = max(0.0, min(1.0, why_min_write_conf))
    why_write_edges = bool(why_cfg.get("write_edges", True))

    # The "segment checkpoint" mechanism is shared infrastructure: it is required for both
    # mining (workflows/preferences/claims) and deterministic node materialization.
    checkpoint_enabled = bool(wf_auto_mine or pref_auto_mine or tdb_auto_mine or tdb_auto_nodes)

    def _flush_state_warnings(*, batch_id: str = "b0.state_recovery") -> None:
        if not state_warnings:
            return
        tid = str(thread_id or hands_state.get("thread_id") or "").strip()
        items = list(state_warnings)
        state_warnings.clear()
        evw.append(
            {
                "kind": "state_corrupt",
                "batch_id": str(batch_id or "b0.state_recovery"),
                "ts": now_rfc3339(),
                "thread_id": tid,
                "items": items,
            }
        )

    segment_max_records = 40
    segment_state: dict[str, Any] = {}
    segment_records: list[dict[str, Any]] = []
    # Avoid inflating mined occurrence counts within a single `mi run` invocation.
    wf_sigs_counted_in_run: set[str] = set()
    pref_sigs_counted_in_run: set[str] = set()

    def _new_segment_state(*, reason: str, thread_hint: str) -> dict[str, Any]:
        return new_segment_state(
            reason=reason,
            thread_hint=thread_hint,
            task=task,
            now_ts=now_rfc3339,
            truncate=_truncate,
            id_factory=lambda: f"seg_{time.time_ns()}_{secrets.token_hex(4)}",
        )

    def _load_segment_state(*, thread_hint: str) -> dict[str, Any] | None:
        return load_segment_state(
            path=project_paths.segment_state_path,
            read_json_best_effort=read_json_best_effort,
            state_warnings=state_warnings,
            thread_hint=thread_hint,
        )

    def _persist_segment_state() -> None:
        persist_segment_state(
            enabled=checkpoint_enabled,
            path=project_paths.segment_state_path,
            segment_state=segment_state,
            segment_max_records=segment_max_records,
            now_ts=now_rfc3339,
            write_json_atomic=write_json_atomic,
        )

    def _clear_segment_state() -> None:
        clear_segment_state(path=project_paths.segment_state_path)

    if checkpoint_enabled:
        # When NOT continuing a Hands session, do not carry over an open segment buffer.
        if not bool(continue_hands) or bool(reset_hands):
            _clear_segment_state()

        seg0 = _load_segment_state(thread_hint=str(thread_id or ""))
        segment_state = seg0 if isinstance(seg0, dict) else _new_segment_state(reason="run_start", thread_hint=str(thread_id or ""))
        recs0 = segment_state.get("records")
        segment_records = recs0 if isinstance(recs0, list) else []
        segment_state["records"] = segment_records

        # Include a workflow trigger marker in the segment when present.
        if matched:
            segment_records.append(evidence_window[-1])
            segment_records[:] = segment_records[-segment_max_records:]
        _persist_segment_state()
        _flush_state_warnings()

    intr = runtime_cfg.get("interrupt") if isinstance(runtime_cfg.get("interrupt"), dict) else {}
    intr_mode = str(intr.get("mode") or "off")
    intr_signals = intr.get("signal_sequence") or ["SIGINT", "SIGTERM", "SIGKILL"]
    intr_escalation = intr.get("escalation_ms") or [2000, 5000]
    interrupt_cfg = (
        InterruptConfig(mode=intr_mode, signal_sequence=[str(s) for s in intr_signals], escalation_ms=[int(x) for x in intr_escalation])
        if intr_mode in ("on_high_risk", "on_any_external")
        else None
    )

    sent_sigs: list[str] = []
    learn_suggested_records_this_run: list[dict[str, Any]] = []

    # Mind circuit breaker:
    # - After repeated consecutive Mind failures, stop issuing further Mind calls
    #   in this `mi run` invocation and converge quickly to user override / blocked.
    mind_failures_total = 0
    mind_failures_consecutive = 0
    mind_circuit_open = False
    mind_circuit_threshold = 2

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
        """Apply or record suggested preference/goal changes (strict Thought DB mode).

        MI no longer treats free-form learned text as canonical. Instead, any auto-learning is
        materialized as Thought DB Claims (append-only) so later decisions can use canonical
        preference/goal claims.

        - When runtime.violation_response.auto_learn is true (default), MI will append preference Claims.
        - When false, MI will NOT write claims; it only records suggestions into EvidenceLog.

        Returns: list of applied claim_ids (empty if none applied).
        """

        vr = runtime_cfg.get("violation_response") if isinstance(runtime_cfg.get("violation_response"), dict) else {}
        auto_learn = bool(vr.get("auto_learn", True))

        # Normalize to a stable, minimal shape (keep severity if present for audit).
        norm: list[dict[str, Any]] = []
        if isinstance(learn_suggested, list):
            for ch in learn_suggested:
                if not isinstance(ch, dict):
                    continue
                scope = str(ch.get("scope") or "").strip()
                text = str(ch.get("text") or "").strip()
                if scope not in ("global", "project") or not text:
                    continue
                item: dict[str, Any] = {
                    "scope": scope,
                    "text": text,
                    "rationale": str(ch.get("rationale") or "").strip(),
                }
                sev = str(ch.get("severity") or "").strip()
                if sev:
                    item["severity"] = sev
                norm.append(item)

        if not norm:
            return []

        suggestion_id = f"ls_{time.time_ns()}_{secrets.token_hex(4)}"
        applied_claim_ids: list[str] = []

        ev_ids = [str(x).strip() for x in (source_event_ids or []) if str(x).strip()][:8]

        # Dedup against existing active canonical preference claims (best-effort).
        # Signature is stable across runs for identical text.
        sig_to_id = {
            "project": tdb.existing_signature_map(scope="project"),
            "global": tdb.existing_signature_map(scope="global"),
        }

        for item in norm:
            scope0 = str(item.get("scope") or "").strip()
            text = str(item.get("text") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            sev = str(item.get("severity") or "").strip()
            if scope0 not in ("global", "project") or not text:
                continue

            sc = "global" if scope0 == "global" else "project"
            pid = project_paths.project_id if sc == "project" else ""
            sig = claim_signature(claim_type="preference", scope=sc, project_id=pid, text=text)
            existing = sig_to_id.get(sc, {}).get(sig)
            if existing:
                applied_claim_ids.append(str(existing))
                continue

            if not auto_learn:
                continue

            tags: list[str] = ["mi:learned", "mi:pref", f"mi:source:{source}"]
            if sev:
                tags.append(f"severity:{sev}")

            base_r = rationale or source
            notes = f"{base_r} (source={source} suggestion={suggestion_id})"
            try:
                cid = tdb.append_claim_create(
                    claim_type="preference",
                    text=text,
                    scope=sc,
                    visibility=("global" if sc == "global" else "project"),
                    valid_from=None,
                    valid_to=None,
                    tags=tags,
                    source_event_ids=ev_ids,
                    confidence=1.0,
                    notes=notes,
                )
            except Exception:
                continue

            sig_to_id.setdefault(sc, {})[sig] = cid
            applied_claim_ids.append(cid)

        rec = evw.append(
            {
                "kind": "learn_suggested",
                "id": suggestion_id,
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "source": source,
                "auto_learn": auto_learn,
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "learn_suggested": norm,
                # Strict Thought DB mode: canonical preference claims.
                "applied_claim_ids": applied_claim_ids,
                "source_event_ids": ev_ids,
            }
        )
        if isinstance(rec, dict):
            learn_suggested_records_this_run.append(rec)

        return applied_claim_ids

    def _log_mind_error(
        *,
        batch_id: str,
        schema_filename: str,
        tag: str,
        error: str,
        mind_transcript_ref: str,
    ) -> None:
        evw.append(
            {
                "kind": "mind_error",
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "schema_filename": str(schema_filename),
                "tag": str(tag),
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "error": _truncate(str(error or ""), 2000),
            }
        )

    def _log_mind_circuit_open(
        *,
        batch_id: str,
        schema_filename: str,
        tag: str,
        error: str,
    ) -> None:
        evw.append(
            {
                "kind": "mind_circuit",
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "state": "open",
                "threshold": mind_circuit_threshold,
                "failures_total": mind_failures_total,
                "failures_consecutive": mind_failures_consecutive,
                "schema_filename": str(schema_filename),
                "tag": str(tag),
                "error": _truncate(str(error or ""), 2000),
            }
        )

    def _mind_call(
        *,
        schema_filename: str,
        prompt: str,
        tag: str,
        batch_id: str,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """Best-effort Mind call wrapper.

        - Never raises (logs to EvidenceLog as kind=mind_error).
        - Circuit breaker: after repeated failures, returns skipped without calling Mind.
        - Returns (obj, mind_transcript_ref, state) where state is ok|error|skipped.
        """

        nonlocal mind_failures_total, mind_failures_consecutive, mind_circuit_open

        if mind_circuit_open:
            return None, "", "skipped"

        try:
            res = llm.call(schema_filename=schema_filename, prompt=prompt, tag=tag)
            obj = getattr(res, "obj", None)
            tp = getattr(res, "transcript_path", None)
            mind_ref = str(tp) if tp else ""
            mind_failures_consecutive = 0
            return (obj if isinstance(obj, dict) else None), mind_ref, "ok"
        except Exception as e:
            mind_ref = ""

            tp = getattr(e, "transcript_path", None)
            if isinstance(tp, Path):
                mind_ref = str(tp)
            elif isinstance(tp, str) and tp.strip():
                mind_ref = tp.strip()
            elif isinstance(e, MindCallError) and e.transcript_path:
                mind_ref = str(e.transcript_path)

            mind_failures_total += 1
            mind_failures_consecutive += 1

            _log_mind_error(
                batch_id=batch_id,
                schema_filename=schema_filename,
                tag=tag,
                error=str(e),
                mind_transcript_ref=mind_ref,
            )
            evidence_window.append(
                {
                    "kind": "mind_error",
                    "batch_id": batch_id,
                    "schema_filename": schema_filename,
                    "tag": tag,
                    "error": _truncate(str(e), 400),
                }
            )
            evidence_window[:] = evidence_window[-8:]

            if not mind_circuit_open and mind_failures_consecutive >= mind_circuit_threshold:
                mind_circuit_open = True
                _log_mind_circuit_open(batch_id=batch_id, schema_filename=schema_filename, tag=tag, error=str(e))
                evidence_window.append(
                    {
                        "kind": "mind_circuit",
                        "batch_id": batch_id,
                        "state": "open",
                        "threshold": mind_circuit_threshold,
                        "failures_consecutive": mind_failures_consecutive,
                        "note": "opened due to repeated mind_error",
                    }
                )
                evidence_window[:] = evidence_window[-8:]

            return None, mind_ref, "error"

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
        out = mem.maybe_cross_project_recall(batch_id=batch_id, reason=reason, query=query, thread_id=str(thread_id or ""))
        if not out:
            return
        rec = evw.append(out.evidence_event)
        win = dict(out.window_entry)
        if isinstance(rec.get("event_id"), str) and rec.get("event_id"):
            win["event_id"] = rec["event_id"]
        evidence_window.append(win)
        evidence_window[:] = evidence_window[-8:]
        _segment_add(rec)
        _persist_segment_state()

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

    def _ensure_testless_strategy_claim_current() -> None:
        """Unify testless verification strategy storage via Thought DB.

        - If a tagged preference Claim exists, derive ProjectOverlay from it (best-effort).
        """
        nonlocal overlay

        as_of = now_rfc3339()
        claim = _find_testless_strategy_claim(as_of_ts=as_of)
        claim_id = str(claim.get("claim_id") or "").strip() if isinstance(claim, dict) else ""
        claim_text = str(claim.get("text") or "").strip() if isinstance(claim, dict) else ""
        claim_strategy = _parse_testless_strategy_from_claim_text(claim_text)

        tls = overlay.get("testless_verification_strategy") if isinstance(overlay, dict) else None
        overlay_chosen = bool(tls.get("chosen_once", False)) if isinstance(tls, dict) else False
        overlay_claim_id = str(tls.get("claim_id") or "").strip() if isinstance(tls, dict) else ""

        if claim_id and claim_strategy:
            # Derive overlay from canonical claim when missing or divergent.
            if (not overlay_chosen) or (overlay_claim_id.strip() != claim_id.strip()):
                overlay.setdefault("testless_verification_strategy", {})
                overlay["testless_verification_strategy"] = {
                    "chosen_once": True,
                    "claim_id": claim_id,
                    "rationale": f"derived from Thought DB {claim_id}",
                }
                write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
                _refresh_overlay_refs()
            return

    # Canonical operational defaults (ask_when_uncertain/refactor_intent) live as global Thought DB preference claims.
    # Runtime config defaults are non-canonical; we only seed missing claims.
    try:
        defaults_sync = ensure_operational_defaults_claims_current(
            home_dir=home,
            tdb=tdb,
            desired_defaults=None,
            mode="seed_missing",
            event_notes="auto_seed_on_run",
            claim_notes_prefix="auto_seed",
        )
    except Exception as e:
        defaults_sync = {"ok": False, "changed": False, "mode": "seed_missing", "event_id": "", "error": f"{type(e).__name__}: {e}"}

    evw.append(
        {
            "kind": "defaults_claim_sync",
            "batch_id": "b0.defaults_claim_sync",
            "ts": now_rfc3339(),
            "thread_id": "",
            "sync": defaults_sync if isinstance(defaults_sync, dict) else {"ok": False, "error": "invalid result"},
        }
    )

    _ensure_testless_strategy_claim_current()

    # Seed one conservative recall at run start so later Mind calls can use it without bothering the user.
    if str(task or "").strip():
        _maybe_cross_project_recall(batch_id="b0.recall", reason="run_start", query=task)

    def _append_check_plan_record(*, batch_id: str, checks_obj: Any, mind_transcript_ref: str) -> dict[str, Any]:
        """Append a check_plan record and keep evidence_window/segment in sync (single source of truth)."""

        obj = checks_obj if isinstance(checks_obj, dict) else _empty_check_plan()
        rec = evw.append(
            {
                "kind": "check_plan",
                "batch_id": str(batch_id),
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "checks": obj,
            }
        )
        evidence_window.append({"kind": "check_plan", "batch_id": str(batch_id), "event_id": rec.get("event_id"), **obj})
        evidence_window[:] = evidence_window[-8:]
        _segment_add({"kind": "check_plan", "batch_id": str(batch_id), "event_id": rec.get("event_id"), **obj})
        _persist_segment_state()
        return rec

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

        checks_prompt = plan_min_checks_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=thought_db_context,
            recent_evidence=evidence_window,
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        )
        checks_obj, mind_ref, state = _mind_call(
            schema_filename="plan_min_checks.json",
            prompt=checks_prompt,
            tag=str(tag or ""),
            batch_id=str(batch_id or ""),
        )
        if checks_obj is None:
            checks_obj = _empty_check_plan()
            checks_obj["notes"] = notes_on_skipped if state == "skipped" else notes_on_error
        return (checks_obj if isinstance(checks_obj, dict) else _empty_check_plan()), str(mind_ref or ""), str(state or "")

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

        if not should_plan:
            checks_obj = _empty_check_plan()
            checks_obj["notes"] = str(notes_on_skip or "").strip()
            checks_ref = ""
            state = "skipped"
        else:
            checks_obj, checks_ref, state = _call_plan_min_checks(
                batch_id=batch_id,
                tag=tag,
                thought_db_context=thought_db_context,
                repo_observation=repo_observation,
                notes_on_skipped=notes_on_skipped,
                notes_on_error=notes_on_error,
            )

        if postprocess and callable(postprocess):
            try:
                out = postprocess(checks_obj, state)
                if isinstance(out, dict):
                    checks_obj = out
            except Exception:
                pass

        _append_check_plan_record(batch_id=batch_id, checks_obj=checks_obj, mind_transcript_ref=checks_ref)
        return checks_obj, checks_ref, state

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

        tls = overlay.get("testless_verification_strategy") if isinstance(overlay, dict) else None
        tls_chosen_once = bool(tls.get("chosen_once", False)) if isinstance(tls, dict) else False

        tls_claim = _find_testless_strategy_claim(as_of_ts=as_of_ts)
        tls_claim_strategy = ""
        tls_claim_id = ""
        if isinstance(tls_claim, dict):
            tls_claim_id = str(tls_claim.get("claim_id") or "").strip()
            tls_claim_strategy = _parse_testless_strategy_from_claim_text(str(tls_claim.get("text") or ""))

        if tls_claim_strategy:
            tls_chosen_once = True
            # Keep overlay aligned (derived cache pointer) and to avoid decide_next prompting.
            cur_cid = str(tls.get("claim_id") or "").strip() if isinstance(tls, dict) else ""
            if tls_claim_id and cur_cid.strip() != tls_claim_id.strip():
                overlay.setdefault("testless_verification_strategy", {})
                overlay["testless_verification_strategy"] = {
                    "chosen_once": True,
                    "claim_id": tls_claim_id,
                    "rationale": f"derived from Thought DB {tls_claim_id}",
                }
                write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
                _refresh_overlay_refs()

        return tls_claim_strategy, tls_claim_id, tls_chosen_once

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

        strategy = str(strategy_text or "").strip()
        if not strategy:
            return ""

        src_eid = str(source_event_id or "").strip()
        if not src_eid:
            rec = evw.append(
                {
                    "kind": "testless_strategy_set",
                    "batch_id": str(fallback_batch_id or "").strip(),
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "strategy": strategy,
                    "rationale": str(claim_rationale or default_rationale or "").strip(),
                }
            )
            src_eid = str(rec.get("event_id") or "").strip()

        tls_cid = _upsert_testless_strategy_claim(
            strategy_text=strategy,
            source_event_id=src_eid,
            source=str(source or "").strip(),
            rationale=str(claim_rationale or default_rationale or "").strip(),
        )

        overlay.setdefault("testless_verification_strategy", {})
        overlay["testless_verification_strategy"] = {
            "chosen_once": True,
            "claim_id": tls_cid,
            "rationale": (f"{overlay_rationale} (canonical claim {tls_cid})" if tls_cid else str(overlay_rationale_default or "").strip()),
        }
        write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
        _refresh_overlay_refs()
        return tls_cid

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

        nonlocal overlay

        def _notes_label(label: str) -> str:
            n = str(notes_prefix or "").strip()
            if n:
                return n + " " + str(label or "").strip()
            return str(label or "").strip()

        tls_claim_strategy, _, tls_chosen_once = _sync_tls_overlay_from_thoughtdb(as_of_ts=now_rfc3339())

        needs_tls = bool(checks_obj.get("needs_testless_strategy", False)) if isinstance(checks_obj, dict) else False
        if needs_tls and not tls_chosen_once:
            q = str(checks_obj.get("testless_strategy_question") or "").strip()
            if not q:
                q = "This project appears to have no tests. What testless verification strategy should MI use for this project (one-time)?"
            answer = _read_user_answer(q)
            if not answer:
                return checks_obj, "user did not provide required input"

            ui = evw.append(
                {
                    "kind": "user_input",
                    "batch_id": str(user_input_batch_id),
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "question": q,
                    "answer": answer,
                }
            )
            evidence_window.append(
                {
                    "kind": "user_input",
                    "batch_id": str(user_input_batch_id),
                    "event_id": ui.get("event_id"),
                    "question": q,
                    "answer": answer,
                }
            )
            evidence_window[:] = evidence_window[-8:]
            _segment_add(ui)
            _persist_segment_state()

            # Canonicalize into Thought DB as a project preference claim.
            src_eid = str(ui.get("event_id") or "").strip()
            _canonicalize_tls_and_update_overlay(
                strategy_text=answer.strip(),
                source_event_id=src_eid,
                fallback_batch_id=str(user_input_batch_id),
                overlay_rationale="user provided",
                overlay_rationale_default="user provided testless verification strategy",
                claim_rationale=rationale,
                default_rationale=rationale,
                source=source,
            )

            # Re-plan checks now that the project has a strategy to follow.
            tdb_ctx2 = _build_decide_context(hands_last_message=hands_last_message, recent_evidence=evidence_window)
            tdb_ctx2_obj = tdb_ctx2.to_prompt_obj()
            checks_obj2, checks_ref2, _ = _plan_checks_and_record(
                batch_id=batch_id_after_testless,
                tag=tag_after_testless,
                thought_db_context=tdb_ctx2_obj,
                repo_observation=repo_observation,
                should_plan=True,
                notes_on_skip="",
                notes_on_skipped=f"skipped: mind_circuit_open (plan_min_checks {_notes_label('after_testless')})",
                notes_on_error=f"mind_error: plan_min_checks({_notes_label('after_testless')}) failed; see EvidenceLog kind=mind_error",
            )
            checks_obj = checks_obj2

            tls_claim_strategy, _, tls_chosen_once = _sync_tls_overlay_from_thoughtdb(as_of_ts=now_rfc3339())

        # If Thought DB already provides a canonical testless strategy but the check planner
        # still requested it, re-plan once (best-effort) to avoid blocking.
        needs_tls2 = bool(checks_obj.get("needs_testless_strategy", False)) if isinstance(checks_obj, dict) else False
        if needs_tls2 and tls_claim_strategy:
            tdb_ctx_tls = _build_decide_context(hands_last_message=hands_last_message, recent_evidence=evidence_window)
            tdb_ctx_tls_obj = tdb_ctx_tls.to_prompt_obj()
            notes_on_skipped = f"skipped: mind_circuit_open (plan_min_checks {_notes_label('after_tls_claim')})"
            notes_on_error = f"mind_error: plan_min_checks({_notes_label('after_tls_claim')}) failed; using Thought DB strategy"

            def _postprocess_after_tls_claim(obj: dict[str, Any], state: str) -> dict[str, Any]:
                if str(state or "") != "ok":
                    # Ensure we don't ask again; proceed with a conservative "no checks" plan.
                    checks_obj["needs_testless_strategy"] = False
                    checks_obj["testless_strategy_question"] = ""
                    base_note = str(checks_obj.get("notes") or "").strip()
                    extra = notes_on_skipped if str(state or "") == "skipped" else notes_on_error
                    checks_obj["notes"] = (base_note + "; " + extra).strip("; ").strip()
                    return checks_obj
                return obj if isinstance(obj, dict) else _empty_check_plan()

            checks_obj3, _, _ = _plan_checks_and_record2(
                batch_id=batch_id_after_tls_claim,
                tag=tag_after_tls_claim,
                thought_db_context=tdb_ctx_tls_obj,
                repo_observation=repo_observation,
                should_plan=True,
                notes_on_skip="",
                notes_on_skipped=notes_on_skipped,
                notes_on_error=notes_on_error,
                postprocess=_postprocess_after_tls_claim,
            )
            checks_obj = checks_obj3

        return checks_obj, ""

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

        if not isinstance(set_tls, dict):
            return

        strategy = str(set_tls.get("strategy") or "").strip()
        rationale = str(set_tls.get("rationale") or "").strip()
        if not strategy:
            return

        _canonicalize_tls_and_update_overlay(
            strategy_text=strategy,
            source_event_id=str(decide_event_id or "").strip(),
            fallback_batch_id=str(fallback_batch_id or "").strip(),
            overlay_rationale=rationale,
            overlay_rationale_default=rationale,
            claim_rationale=rationale or str(default_rationale or "").strip(),
            default_rationale=str(default_rationale or "").strip(),
            source=str(source or "").strip(),
        )

    def _mine_workflow_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        if not bool(wf_auto_mine):
            return
        if executed_batches <= 0:
            return

        auto_enable = bool(wf_cfg.get("auto_enable", True))
        auto_sync = bool(wf_cfg.get("auto_sync_on_change", True))
        allow_single_high = bool(wf_cfg.get("allow_single_if_high_benefit", True))
        try:
            min_occ = int(wf_cfg.get("min_occurrences", 2) or 2)
        except Exception:
            min_occ = 2
        if min_occ < 1:
            min_occ = 1

        mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
        tdb_ctx = _build_decide_context(hands_last_message="", recent_evidence=seg_evidence[-8:])
        tdb_ctx_obj = tdb_ctx.to_prompt_obj()
        prompt = suggest_workflow_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_obj,
            recent_evidence=seg_evidence,
            notes=mine_notes,
        )
        out, mind_ref, state = _mind_call(
            schema_filename="suggest_workflow.json",
            prompt=prompt,
            tag=f"suggest_workflow:{base_batch_id}",
            batch_id=f"{base_batch_id}.workflow_suggestion",
        )

        evw.append(
            {
                "kind": "workflow_suggestion",
                "batch_id": f"{base_batch_id}.workflow_suggestion",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "state": state,
                "mind_transcript_ref": mind_ref,
                "notes": mine_notes,
                "output": out if isinstance(out, dict) else {},
            }
        )

        if not isinstance(out, dict):
            return
        if not bool(out.get("should_suggest", False)):
            return
        sug = out.get("suggestion")
        if not isinstance(sug, dict):
            return

        signature = str(sug.get("signature") or "").strip()
        if not signature:
            return
        benefit = str(sug.get("benefit") or "").strip()
        reason_s = str(sug.get("reason") or "").strip()
        confidence = sug.get("confidence")
        try:
            conf_f = float(confidence) if confidence is not None else 0.0
        except Exception:
            conf_f = 0.0

        candidates = load_workflow_candidates(project_paths, warnings=state_warnings)
        by_sig = candidates.get("by_signature") if isinstance(candidates.get("by_signature"), dict) else {}
        entry = by_sig.get(signature)
        if not isinstance(entry, dict):
            entry = {}

        try:
            prev_n = int(entry.get("count") or 0)
        except Exception:
            prev_n = 0
        already_counted = signature in wf_sigs_counted_in_run
        if already_counted:
            new_n = prev_n
        else:
            new_n = prev_n + 1
            wf_sigs_counted_in_run.add(signature)
        entry["count"] = new_n
        entry["last_ts"] = now_rfc3339()
        entry["benefit"] = benefit
        entry["confidence"] = conf_f
        if reason_s:
            entry["reason"] = reason_s
        wf_obj = sug.get("workflow") if isinstance(sug.get("workflow"), dict) else {}
        name = str(wf_obj.get("name") or "").strip()
        if name:
            entry["workflow_name"] = name

        by_sig[signature] = entry
        candidates["by_signature"] = by_sig
        write_workflow_candidates(project_paths, candidates)
        _flush_state_warnings()

        existing_wid = str(entry.get("workflow_id") or "").strip()
        if existing_wid:
            return

        threshold = min_occ
        if allow_single_high and benefit == "high":
            threshold = 1
        if new_n < threshold:
            return

        if not isinstance(wf_obj, dict) or not wf_obj:
            return

        wid = new_workflow_id()
        wf_final = dict(wf_obj)
        wf_final["id"] = wid
        wf_final["enabled"] = bool(auto_enable)

        src = wf_final.get("source") if isinstance(wf_final.get("source"), dict) else {}
        ev_refs = src.get("evidence_refs") if isinstance(src.get("evidence_refs"), list) else []
        wf_final["source"] = {
            "kind": "suggested",
            "reason": (reason_s or "suggest_workflow") + f" (signature={signature} benefit={benefit} confidence={conf_f:.2f})",
            "evidence_refs": [str(x) for x in ev_refs if str(x).strip()],
        }
        wf_final["created_ts"] = now_rfc3339()
        wf_final["updated_ts"] = now_rfc3339()

        wf_store.write(wf_final)

        entry["workflow_id"] = wid
        entry["solidified_ts"] = now_rfc3339()
        by_sig[signature] = entry
        candidates["by_signature"] = by_sig
        write_workflow_candidates(project_paths, candidates)

        evw.append(
            {
                "kind": "workflow_solidified",
                "batch_id": f"{base_batch_id}.workflow_solidified",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "signature": signature,
                "count": new_n,
                "threshold": threshold,
                "benefit": benefit,
                "confidence": conf_f,
                "workflow_id": wid,
                "workflow_name": str(wf_final.get("name") or ""),
                "enabled": bool(wf_final.get("enabled", False)),
            }
        )

        if auto_sync:
            effective = wf_registry.enabled_workflows_effective(overlay=overlay)
            effective = [{k: v for k, v in w.items() if k != "_mi_scope"} for w in effective if isinstance(w, dict)]
            sync_obj = sync_hosts_from_overlay(
                overlay=overlay,
                project_id=project_paths.project_id,
                workflows=effective,
                warnings=state_warnings,
            )
            evw.append(
                {
                    "kind": "host_sync",
                    "batch_id": f"{base_batch_id}.host_sync",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id or "",
                    "source": "workflow_solidified",
                    "sync": sync_obj,
                }
            )
            _flush_state_warnings()

    def _mine_preferences_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        nonlocal overlay

        if not bool(pref_auto_mine):
            return
        if executed_batches <= 0:
            return

        pref_allow_single_high = bool(pref_cfg.get("allow_single_if_high_benefit", True))
        try:
            pref_min_occ = int(pref_cfg.get("min_occurrences", 2) or 2)
        except Exception:
            pref_min_occ = 2
        if pref_min_occ < 1:
            pref_min_occ = 1
        try:
            pref_min_conf = float(pref_cfg.get("min_confidence", 0.75) or 0.75)
        except Exception:
            pref_min_conf = 0.75
        try:
            pref_max = int(pref_cfg.get("max_suggestions", 3) or 3)
        except Exception:
            pref_max = 3
        if pref_max < 0:
            pref_max = 0
        if pref_max > 10:
            pref_max = 10
        if pref_max == 0:
            return

        mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
        tdb_ctx = _build_decide_context(hands_last_message="", recent_evidence=seg_evidence[-8:])
        tdb_ctx_obj = tdb_ctx.to_prompt_obj()
        prompt = mine_preferences_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_obj,
            recent_evidence=seg_evidence,
            notes=mine_notes,
        )
        out, mind_ref, state = _mind_call(
            schema_filename="mine_preferences.json",
            prompt=prompt,
            tag=f"mine_preferences:{base_batch_id}",
            batch_id=f"{base_batch_id}.preference_mining",
        )

        evw.append(
            {
                "kind": "preference_mining",
                "batch_id": f"{base_batch_id}.preference_mining",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "state": state,
                "mind_transcript_ref": mind_ref,
                "notes": mine_notes,
                "output": out if isinstance(out, dict) else {},
            }
        )

        if not isinstance(out, dict):
            return
        sugs = out.get("suggestions")
        if not isinstance(sugs, list) or not sugs:
            return

        candidates = load_preference_candidates(project_paths, warnings=state_warnings)
        by_sig = candidates.get("by_signature") if isinstance(candidates.get("by_signature"), dict) else {}

        # Evidence provenance for preference claims (best-effort): cite recent segment events.
        src_eids_pref: list[str] = []
        seen_eids: set[str] = set()
        for r in (seg_evidence or [])[-16:]:
            if not isinstance(r, dict):
                continue
            eid = r.get("event_id")
            if not isinstance(eid, str):
                continue
            e = eid.strip()
            if not e or e in seen_eids:
                continue
            seen_eids.add(e)
            src_eids_pref.append(e)

        # Skip obvious duplicates against existing canonical preference claims (best-effort).
        existing_sig_to_id = {
            "project": tdb.existing_signature_map(scope="project"),
            "global": tdb.existing_signature_map(scope="global"),
        }

        for raw in sugs[:pref_max]:
            if not isinstance(raw, dict):
                continue
            scope = str(raw.get("scope") or "project").strip()
            if scope not in ("global", "project"):
                scope = "project"
            text = str(raw.get("text") or "").strip()
            if not text:
                continue

            pid = project_paths.project_id if scope == "project" else ""
            sig2 = claim_signature(claim_type="preference", scope=scope, project_id=pid, text=text)
            if sig2 in existing_sig_to_id.get(scope, {}):
                continue

            benefit = str(raw.get("benefit") or "medium").strip()
            if benefit not in ("low", "medium", "high"):
                benefit = "medium"
            rationale = str(raw.get("rationale") or "").strip()
            conf = raw.get("confidence")
            try:
                conf_f = float(conf) if conf is not None else 0.0
            except Exception:
                conf_f = 0.0
            if conf_f < pref_min_conf:
                continue

            sig = preference_signature(scope=scope, text=text)
            entry = by_sig.get(sig)
            if not isinstance(entry, dict):
                entry = {}

            try:
                prev_n = int(entry.get("count") or 0)
            except Exception:
                prev_n = 0
            already_counted = sig in pref_sigs_counted_in_run
            if already_counted:
                new_n = prev_n
            else:
                new_n = prev_n + 1
                pref_sigs_counted_in_run.add(sig)
            entry["count"] = new_n
            entry["last_ts"] = now_rfc3339()
            entry["scope"] = scope
            entry["text"] = text
            entry["benefit"] = benefit
            entry["confidence"] = conf_f
            if rationale:
                entry["rationale"] = rationale

            if bool(entry.get("suggestion_emitted", False)) or bool(entry.get("applied_claim_ids")):
                by_sig[sig] = entry
                continue

            threshold = pref_min_occ
            if pref_allow_single_high and benefit == "high":
                threshold = 1
            if new_n < threshold:
                by_sig[sig] = entry
                continue

            applied_ids = _handle_learn_suggested(
                learn_suggested=[{"scope": scope, "text": text, "rationale": rationale or "preference_mining", "severity": "medium"}],
                batch_id=f"{base_batch_id}.preference_solidified",
                source="mine_preferences",
                mind_transcript_ref=mind_ref,
                source_event_ids=src_eids_pref,
            )
            entry["suggestion_emitted"] = True
            entry["suggestion_ts"] = now_rfc3339()
            if applied_ids:
                entry["applied_claim_ids"] = list(applied_ids)
                entry["solidified_ts"] = now_rfc3339()

            evw.append(
                {
                    "kind": "preference_solidified",
                    "batch_id": f"{base_batch_id}.preference_solidified",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id or "",
                    "signature": sig,
                    "count": new_n,
                    "threshold": threshold,
                    "benefit": benefit,
                    "confidence": conf_f,
                    "scope": scope,
                    "text": text,
                    "applied_claim_ids": list(applied_ids),
                }
            )
            by_sig[sig] = entry

        candidates["by_signature"] = by_sig
        write_preference_candidates(project_paths, candidates)
        _flush_state_warnings()

    def _mine_claims_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        """Mine high-signal atomic Claims into Thought DB (checkpoint-only; best-effort)."""

        if not bool(tdb_auto_mine):
            return
        if executed_batches <= 0:
            return
        if tdb_max_claims <= 0:
            return

        # Allowed citations for source_refs: EvidenceLog event_id only.
        allowed: list[str] = []
        seen: set[str] = set()
        for rec in seg_evidence or []:
            if not isinstance(rec, dict):
                continue
            eid = rec.get("event_id")
            if not isinstance(eid, str):
                continue
            e = eid.strip()
            if not e or e in seen:
                continue
            seen.add(e)
            allowed.append(e)
        allowed_set = set(allowed)

        mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
        tdb_ctx = _build_decide_context(hands_last_message="", recent_evidence=seg_evidence[-8:])
        tdb_ctx_obj = tdb_ctx.to_prompt_obj()
        prompt = mine_claims_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_obj,
            segment_evidence=seg_evidence,
            allowed_event_ids=allowed,
            min_confidence=tdb_min_conf,
            max_claims=tdb_max_claims,
            notes=mine_notes,
        )
        out, mind_ref, state = _mind_call(
            schema_filename="mine_claims.json",
            prompt=prompt,
            tag=f"mine_claims:{base_batch_id}",
            batch_id=f"{base_batch_id}.claim_mining",
        )

        applied: dict[str, Any] = {"written": [], "skipped": []}
        if isinstance(out, dict):
            applied = tdb.apply_mined_output(
                output=out,
                allowed_event_ids=allowed_set,
                min_confidence=tdb_min_conf,
                max_claims=tdb_max_claims,
            )

        evw.append(
            {
                "kind": "claim_mining",
                "batch_id": f"{base_batch_id}.claim_mining",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "segment_id": str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else "",
                "state": state,
                "mind_transcript_ref": mind_ref,
                "notes": mine_notes,
                "config": {
                    "min_confidence": tdb_min_conf,
                    "max_claims_per_checkpoint": tdb_max_claims,
                },
                "output": out if isinstance(out, dict) else {},
                "applied": applied,
            }
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
        """Materialize Decision/Action/Summary nodes at a checkpoint (deterministic; best-effort).

        This does NOT add any new mind calls. It only uses EvidenceLog-derived records that already exist:
        - snapshot record (created at checkpoint)
        - segment evidence (evidence + decide_next + ...)
        """

        if not bool(tdb_enabled) or not bool(tdb_auto_nodes):
            return

        # Collect candidate source event ids (EvidenceLog event_id only).
        src_ids: list[str] = []
        seen_src: set[str] = set()

        def add_src(eid: str) -> None:
            s = str(eid or "").strip()
            if not s or s in seen_src:
                return
            seen_src.add(s)
            src_ids.append(s)

        snap_event_id = ""
        snap_text = ""
        snap_task = ""
        snap_tags: list[str] = []
        if isinstance(snapshot_rec, dict):
            snap_event_id = str(snapshot_rec.get("event_id") or "").strip()
            snap_text = str(snapshot_rec.get("text") or "").strip()
            snap_task = str(snapshot_rec.get("task_hint") or "").strip()
            tags = snapshot_rec.get("tags") if isinstance(snapshot_rec.get("tags"), list) else []
            snap_tags = [str(x).strip() for x in tags if str(x).strip()][:12]
        if snap_event_id:
            add_src(snap_event_id)

        # Find the last decide_next in the segment (best-effort).
        last_decide: dict[str, Any] | None = None
        last_seq = -1
        for rec in seg_evidence or []:
            if not isinstance(rec, dict):
                continue
            if str(rec.get("kind") or "").strip() != "decide_next":
                continue
            seq = rec.get("seq")
            try:
                seq_i = int(seq) if seq is not None else -1
            except Exception:
                seq_i = -1
            if seq_i >= last_seq:
                last_seq = seq_i
                last_decide = rec
        if last_decide is None:
            # Fallback: last in list order.
            for rec in reversed(seg_evidence or []):
                if isinstance(rec, dict) and str(rec.get("kind") or "").strip() == "decide_next":
                    last_decide = rec
                    break

        decide_event_id = ""
        decide_status = ""
        decide_next_action = ""
        decide_notes = ""
        if isinstance(last_decide, dict):
            decide_event_id = str(last_decide.get("event_id") or "").strip()
            decide_status = str(last_decide.get("status") or "").strip()
            decide_next_action = str(last_decide.get("next_action") or "").strip()
            decide_notes = str(last_decide.get("notes") or "").strip()
        if decide_event_id:
            add_src(decide_event_id)

        # Aggregate actions from evidence records in this segment.
        action_lines: list[str] = []
        action_src_event_ids: list[str] = []
        seen_actions: set[str] = set()
        for rec in seg_evidence or []:
            if not isinstance(rec, dict):
                continue
            if str(rec.get("kind") or "").strip() != "evidence":
                continue
            eid = str(rec.get("event_id") or "").strip()
            acts = rec.get("actions") if isinstance(rec.get("actions"), list) else []
            for a in acts[:20]:
                s = str(a or "").strip()
                if not s or s in seen_actions:
                    continue
                seen_actions.add(s)
                action_lines.append(s)
                if eid:
                    action_src_event_ids.append(eid)
        for eid in action_src_event_ids[:12]:
            add_src(eid)

        # Create nodes (project scope only for now).
        written_nodes: list[dict[str, str]] = []
        written_edges: list[dict[str, str]] = []
        index_items: list[Any] = []
        base_node_refs = [{"kind": "evidence_event", "event_id": x} for x in src_ids[:12] if str(x).strip()]

        def write_edge(*, edge_type: str, frm: str, to: str, source_eids: list[str], notes: str) -> None:
            if not frm or not to:
                return
            try:
                eid = tdb.append_edge(
                    edge_type=edge_type,
                    from_id=frm,
                    to_id=to,
                    scope="project",
                    visibility="project",
                    source_event_ids=[x for x in source_eids if str(x).strip()][:8],
                    notes=notes,
                )
                written_edges.append({"edge_id": eid, "edge_type": edge_type, "from_id": frm, "to_id": to})
            except Exception:
                return

        ok = True
        err = ""
        try:
            # Summary node: always when we have a snapshot record.
            if snap_text:
                title = f"Summary ({checkpoint_kind or 'checkpoint'}): {snap_task or task}".strip()
                text = "\n".join(
                    [
                        f"checkpoint_kind: {checkpoint_kind or ''}".strip(),
                        f"status_hint: {status_hint or ''}".strip(),
                        f"batch_id: {base_batch_id}".strip(),
                        "",
                        snap_text.strip(),
                    ]
                ).strip()
                tags = ["auto", "checkpoint", "node:summary"]
                if checkpoint_kind:
                    tags.append("checkpoint_kind:" + str(checkpoint_kind))
                if status_hint:
                    tags.append("status:" + str(status_hint))
                tags.extend([f"snapshot_tag:{t}" for t in snap_tags[:6] if t])
                nid = tdb.append_node_create(
                    node_type="summary",
                    title=title,
                    text=text,
                    scope="project",
                    visibility="project",
                    tags=tags,
                    source_event_ids=src_ids[:12],
                    confidence=1.0,
                    notes="auto materialize (snapshot)",
                )
                written_nodes.append({"node_id": nid, "node_type": "summary"})
                if snap_event_id:
                    write_edge(
                        edge_type="derived_from",
                        frm=nid,
                        to=snap_event_id,
                        source_eids=[snap_event_id],
                        notes="auto derived_from snapshot",
                    )
                try:
                    index_items.append(
                        thoughtdb_node_item(
                            node_id=nid,
                            node_type="summary",
                            title=title,
                            text=text,
                            scope="project",
                            project_id=project_paths.project_id,
                            ts=now_rfc3339(),
                            visibility="project",
                            tags=tags,
                            nodes_path=project_paths.thoughtdb_nodes_path,
                            source_refs=base_node_refs,
                        )
                    )
                except Exception:
                    pass

            # Decision node: only when we have a decide_next record.
            if decide_next_action or decide_notes or decide_status:
                title = f"Decision: {decide_next_action or 'unknown'} ({decide_status or 'unknown'})".strip()
                text = "\n".join(
                    [
                        f"status: {decide_status}".strip(),
                        f"next_action: {decide_next_action}".strip(),
                        (f"planned_next_input: {_truncate(planned_next_input or '', 1200)}" if planned_next_input else "").strip(),
                        (f"notes: {decide_notes}" if decide_notes else "").strip(),
                    ]
                ).strip()
                tags = ["auto", "checkpoint", "node:decision"]
                if decide_next_action:
                    tags.append("next_action:" + decide_next_action)
                if decide_status:
                    tags.append("status:" + decide_status)
                nid = tdb.append_node_create(
                    node_type="decision",
                    title=title,
                    text=text,
                    scope="project",
                    visibility="project",
                    tags=tags,
                    source_event_ids=src_ids[:12],
                    confidence=1.0,
                    notes="auto materialize (decide_next)",
                )
                written_nodes.append({"node_id": nid, "node_type": "decision"})
                if decide_event_id:
                    write_edge(
                        edge_type="derived_from",
                        frm=nid,
                        to=decide_event_id,
                        source_eids=[decide_event_id],
                        notes="auto derived_from decide_next",
                    )
                try:
                    index_items.append(
                        thoughtdb_node_item(
                            node_id=nid,
                            node_type="decision",
                            title=title,
                            text=text,
                            scope="project",
                            project_id=project_paths.project_id,
                            ts=now_rfc3339(),
                            visibility="project",
                            tags=tags,
                            nodes_path=project_paths.thoughtdb_nodes_path,
                            source_refs=base_node_refs,
                        )
                    )
                except Exception:
                    pass

            # Action node: only when evidence recorded non-empty actions.
            if action_lines:
                head = action_lines[0] if action_lines else ""
                title = f"Actions: {_truncate(head, 120)}".strip()
                body = "\n".join([f"- {a}" for a in action_lines[:24] if str(a).strip()]).strip()
                text = "\n".join(
                    [
                        f"batch_id: {base_batch_id}".strip(),
                        "",
                        body,
                    ]
                ).strip()
                tags = ["auto", "checkpoint", "node:action"]
                nid = tdb.append_node_create(
                    node_type="action",
                    title=title,
                    text=text,
                    scope="project",
                    visibility="project",
                    tags=tags,
                    source_event_ids=src_ids[:12],
                    confidence=1.0,
                    notes="auto materialize (segment actions)",
                )
                written_nodes.append({"node_id": nid, "node_type": "action"})
                # Link to the evidence events that contained actions.
                for eid in action_src_event_ids[:12]:
                    if eid:
                        write_edge(
                            edge_type="derived_from",
                            frm=nid,
                            to=eid,
                            source_eids=[eid],
                            notes="auto derived_from evidence(actions)",
                        )
                try:
                    index_items.append(
                        thoughtdb_node_item(
                            node_id=nid,
                            node_type="action",
                            title=title,
                            text=text,
                            scope="project",
                            project_id=project_paths.project_id,
                            ts=now_rfc3339(),
                            visibility="project",
                            tags=tags,
                            nodes_path=project_paths.thoughtdb_nodes_path,
                            source_refs=base_node_refs,
                        )
                    )
                except Exception:
                    pass
        except Exception as e:
            ok = False
            err = f"{type(e).__name__}: {e}"

        # Index the created nodes for recall (best-effort; derived).
        if index_items:
            try:
                mem.upsert_items([x for x in index_items if x])
            except Exception:
                pass

        # Record the materialization attempt in EvidenceLog for audit (best-effort).
        try:
            evw.append(
                {
                    "kind": "node_materialized",
                    "batch_id": f"{base_batch_id}.node_materialized",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id or "",
                    "segment_id": str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else "",
                    "checkpoint_kind": str(checkpoint_kind or ""),
                    "status_hint": str(status_hint or ""),
                    "note": (note or "").strip(),
                    "ok": bool(ok),
                    "error": _truncate(err, 400),
                    "snapshot_event_id": snap_event_id,
                    "decide_next_event_id": decide_event_id,
                    "source_event_ids": src_ids[:20],
                    "written_nodes": written_nodes,
                    "written_edges": written_edges,
                }
            )
        except Exception:
            return

    _last_checkpoint_key = ""

    def _maybe_checkpoint_and_mine(*, batch_id: str, planned_next_input: str, status_hint: str, note: str) -> None:
        """LLM-judged checkpoint: may mine workflows/preferences and reset segment buffer."""

        nonlocal segment_state, segment_records, _last_checkpoint_key

        if not checkpoint_enabled:
            return
        if not isinstance(segment_records, list):
            return
        base_bid = str(batch_id or "").split(".", 1)[0].strip()
        if not base_bid:
            return
        # Guard: avoid duplicate checkpoint calls for the same base batch in the same run phase.
        key = base_bid + ":" + str(status_hint or "").strip()
        if key == _last_checkpoint_key:
            return
        _last_checkpoint_key = key

        # Keep thread affinity updated best-effort.
        if isinstance(segment_state, dict):
            cur_tid = str(thread_id or "").strip()
            if cur_tid and cur_tid != "unknown":
                segment_state["thread_id"] = cur_tid
            segment_state["task_hint"] = _truncate(task.strip(), 200)

        tdb_ctx = _build_decide_context(hands_last_message="", recent_evidence=segment_records[-8:])
        tdb_ctx_obj = tdb_ctx.to_prompt_obj()
        prompt = checkpoint_decide_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_obj,
            segment_evidence=segment_records,
            current_batch_id=base_bid,
            planned_next_input=_truncate(planned_next_input or "", 2000),
            status_hint=str(status_hint or ""),
            notes=(note or "").strip(),
        )
        out, mind_ref, state = _mind_call(
            schema_filename="checkpoint_decide.json",
            prompt=prompt,
            tag=f"checkpoint:{base_bid}",
            batch_id=f"{base_bid}.checkpoint",
        )

        evw.append(
            {
                "kind": "checkpoint",
                "batch_id": f"{base_bid}.checkpoint",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "segment_id": str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else "",
                "state": state,
                "mind_transcript_ref": mind_ref,
                "planned_next_input": _truncate(planned_next_input or "", 800),
                "status_hint": str(status_hint or ""),
                "note": (note or "").strip(),
                "output": out if isinstance(out, dict) else {},
            }
        )

        if not isinstance(out, dict):
            _persist_segment_state()
            return

        should_cp = bool(out.get("should_checkpoint", False))
        if not should_cp:
            _persist_segment_state()
            return

        # Mine before resetting segment.
        if bool(out.get("should_mine_workflow", False)):
            _mine_workflow_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")
        if bool(out.get("should_mine_preferences", False)):
            _mine_preferences_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")
        _mine_claims_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")

        # Materialize a compact snapshot for cross-project recall (append-only; traceable to segment records).
        snap = mem.materialize_snapshot(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=segment_records,
            batch_id=f"{base_bid}.snapshot",
            thread_id=str(thread_id or ""),
            task_fallback=task,
            checkpoint_kind=str(out.get("checkpoint_kind") or ""),
            status_hint=str(status_hint or ""),
            checkpoint_notes=str(out.get("notes") or ""),
        )
        snap_rec: dict[str, Any] | None = None
        if snap:
            rec = evw.append(snap.evidence_event)
            snap_rec = rec if isinstance(rec, dict) else None
            win = dict(snap.window_entry)
            if isinstance(rec.get("event_id"), str) and rec.get("event_id"):
                win["event_id"] = rec["event_id"]
            evidence_window.append(win)
            evidence_window[:] = evidence_window[-8:]

        # Deterministic Thought DB nodes: Decision/Action/Summary (no extra mind calls).
        _materialize_nodes_from_checkpoint(
            seg_evidence=segment_records,
            snapshot_rec=snap_rec,
            base_batch_id=base_bid,
            checkpoint_kind=str(out.get("checkpoint_kind") or ""),
            status_hint=str(status_hint or ""),
            planned_next_input=str(planned_next_input or ""),
            note=(note or "").strip(),
        )

        # Reset segment buffer for the next phase.
        segment_state = _new_segment_state(reason=f"checkpoint:{out.get('checkpoint_kind')}", thread_hint=str(thread_id or ""))
        segment_records = segment_state.get("records") if isinstance(segment_state.get("records"), list) else []
        segment_state["records"] = segment_records
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

        chk_text = _get_check_input(existing_check_plan if isinstance(existing_check_plan, dict) else None)
        if chk_text:
            return chk_text, ""

        checks_obj2, checks_ref2, _ = _plan_checks_and_record(
            batch_id=f"{base_batch_id}.loop_break_checks",
            tag=f"checks_loopbreak:{base_batch_id}",
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            should_plan=True,
            notes_on_skip="",
            notes_on_skipped="skipped: mind_circuit_open (plan_min_checks loop_break)",
            notes_on_error="mind_error: plan_min_checks(loop_break) failed; see EvidenceLog kind=mind_error",
        )

        checks_obj2, block_reason = _resolve_tls_for_checks(
            checks_obj=checks_obj2 if isinstance(checks_obj2, dict) else _empty_check_plan(),
            hands_last_message=hands_last_message,
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            user_input_batch_id=f"{base_batch_id}.loop_break",
            batch_id_after_testless=f"{base_batch_id}.loop_break_after_testless",
            batch_id_after_tls_claim=f"{base_batch_id}.loop_break_after_tls_claim",
            tag_after_testless=f"checks_loopbreak_after_tls:{base_batch_id}",
            tag_after_tls_claim=f"checks_loopbreak_after_tls_claim:{base_batch_id}",
            notes_prefix="loop_break",
            source="user_input:testless_strategy(loop_break)",
            rationale="user provided testless verification strategy (loop_break)",
        )
        if block_reason:
            return "", block_reason

        return _get_check_input(checks_obj2 if isinstance(checks_obj2, dict) else None), ""

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

        candidate = (nxt or "").strip()
        if not candidate:
            status = "blocked"
            notes = f"{reason}: empty next input"
            return False

        sig = _loop_sig(hands_last_message=hands_last_message, next_input=candidate)
        sent_sigs.append(sig)
        sent_sigs = sent_sigs[-6:]

        pattern = _loop_pattern(sent_sigs)
        if pattern:
            evw.append(
                {
                    "kind": "loop_guard",
                    "batch_id": batch_id,
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "pattern": pattern,
                    "hands_last_message": _truncate(hands_last_message, 800),
                    "next_input": _truncate(candidate, 800),
                    "reason": reason,
                }
            )
            evidence_window.append({"kind": "loop_guard", "batch_id": batch_id, "pattern": pattern, "reason": reason})
            evidence_window[:] = evidence_window[-8:]

            ask_when_uncertain = bool(resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339()).ask_when_uncertain)

            # Best-effort automatic loop breaking: prefer rewriting the next instruction or forcing checks
            # before asking the user (minimize burden; avoid protocol tyranny).
            lb_prompt = loop_break_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=_mindspec_base_runtime(),
                project_overlay=overlay,
                thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
                recent_evidence=evidence_window,
                repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
                loop_pattern=pattern,
                loop_reason=reason,
                hands_last_message=hands_last_message,
                planned_next_input=candidate,
            )
            lb_obj, lb_ref, lb_state = _mind_call(
                schema_filename="loop_break.json",
                prompt=lb_prompt,
                tag=f"loopbreak:{batch_id}",
                batch_id=batch_id,
            )

            lb_rec = evw.append(
                {
                    "kind": "loop_break",
                    "batch_id": batch_id,
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "pattern": pattern,
                    "reason": reason,
                    "state": lb_state,
                    "mind_transcript_ref": lb_ref,
                    "output": lb_obj if isinstance(lb_obj, dict) else {},
                }
            )
            evidence_window.append(
                {
                    "kind": "loop_break",
                    "batch_id": batch_id,
                    "event_id": lb_rec.get("event_id"),
                    "pattern": pattern,
                    "state": lb_state,
                    "action": (lb_obj.get("action") if isinstance(lb_obj, dict) else ""),
                    "reason": reason,
                }
            )
            evidence_window[:] = evidence_window[-8:]
            _segment_add(lb_rec)
            _persist_segment_state()

            action = str(lb_obj.get("action") or "").strip() if isinstance(lb_obj, dict) else ""

            # Helper: ask user override, used only as a last resort.
            def _prompt_user_override(question: str) -> str:
                override = _read_user_answer(question)
                ui = evw.append(
                    {
                        "kind": "user_input",
                        "batch_id": batch_id,
                        "ts": now_rfc3339(),
                        "thread_id": thread_id,
                        "question": question,
                        "answer": override,
                    }
                )
                evidence_window.append(
                    {
                        "kind": "user_input",
                        "batch_id": batch_id,
                        "event_id": ui.get("event_id"),
                        "question": question,
                        "answer": override,
                    }
                )
                evidence_window[:] = evidence_window[-8:]
                _segment_add(ui)
                _persist_segment_state()
                return override

            if action == "stop_done":
                status = "done"
                notes = f"loop_break: stop_done ({reason})"
                return False

            if action == "stop_blocked":
                status = "blocked"
                notes = f"loop_break: stop_blocked ({reason})"
                return False

            if action == "rewrite_next_input":
                rewritten = str(lb_obj.get("rewritten_next_input") or "").strip() if isinstance(lb_obj, dict) else ""
                if rewritten:
                    candidate = rewritten
                    sent_sigs.clear()
                else:
                    action = ""  # fall through to fallback

            if action == "run_checks_then_continue":
                chk_text, block_reason = _loop_break_get_checks_input(
                    base_batch_id=batch_id,
                    hands_last_message=hands_last_message,
                    thought_db_context=thought_db_context,
                    repo_observation=repo_observation,
                    existing_check_plan=check_plan,
                )
                if block_reason:
                    status = "blocked"
                    notes = block_reason
                    return False

                if chk_text:
                    candidate = chk_text
                    sent_sigs.clear()
                else:
                    action = ""  # fall through

            if action == "ask_user":
                if ask_when_uncertain:
                    q = str(lb_obj.get("ask_user_question") or "").strip() if isinstance(lb_obj, dict) else ""
                    if not q:
                        q = (
                            "MI detected a repeated loop (pattern="
                            + pattern
                            + "). Provide a new instruction to send to Hands, or type 'stop' to end:"
                        )
                    override = _prompt_user_override(q)
                    ov = override.strip()
                    if not ov or ov.lower() in ("stop", "quit", "q"):
                        status = "blocked"
                        notes = "stopped by loop_guard"
                        return False
                    candidate = ov
                    sent_sigs.clear()
                else:
                    status = "blocked"
                    notes = "loop_guard triggered (ask_when_uncertain=false)"
                    return False

            if not action:
                if ask_when_uncertain:
                    q = (
                        "MI detected a repeated loop (pattern="
                        + pattern
                        + "). Provide a new instruction to send to Hands, or type 'stop' to end:"
                    )
                    override = _prompt_user_override(q)
                    ov = override.strip()
                    if not ov or ov.lower() in ("stop", "quit", "q"):
                        status = "blocked"
                        notes = "stopped by loop_guard"
                        return False
                    candidate = ov
                    sent_sigs.clear()
                else:
                    status = "blocked"
                    notes = "loop_guard triggered"
                    return False

        # Checkpoint after the current batch, before sending the next instruction to Hands.
        _maybe_checkpoint_and_mine(
            batch_id=batch_id,
            planned_next_input=candidate,
            status_hint="not_done",
            note="before_continue: " + reason,
        )

        next_input = candidate
        status = "not_done"
        notes = reason
        return True

    def _append_user_input_record(*, batch_id: str, question: str, answer: str) -> dict[str, Any]:
        """Append user input evidence and keep segment/evidence windows in sync."""

        nonlocal evidence_window
        ui = evw.append(
            {
                "kind": "user_input",
                "batch_id": str(batch_id),
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "question": question,
                "answer": answer,
            }
        )
        append_evidence_window(
            evidence_window,
            {"kind": "user_input", "batch_id": str(batch_id), "event_id": ui.get("event_id"), "question": question, "answer": answer},
        )
        segment_add_and_persist(segment_add=_segment_add, persist_segment_state=_persist_segment_state, item=ui)
        return ui

    def _append_auto_answer_record(*, batch_id: str, mind_transcript_ref: str, auto_answer: dict[str, Any]) -> dict[str, Any]:
        """Append auto_answer evidence and keep segment/evidence windows in sync."""

        nonlocal evidence_window
        rec = evw.append(
            {
                "kind": "auto_answer",
                "batch_id": str(batch_id),
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "auto_answer": auto_answer if isinstance(auto_answer, dict) else {},
            }
        )
        append_evidence_window(
            evidence_window,
            {"kind": "auto_answer", "batch_id": str(batch_id), "event_id": rec.get("event_id"), **(auto_answer if isinstance(auto_answer, dict) else {})},
        )
        segment_add_and_persist(
            segment_add=_segment_add,
            persist_segment_state=_persist_segment_state,
            item={"kind": "auto_answer", "batch_id": str(batch_id), "event_id": rec.get("event_id"), **(auto_answer if isinstance(auto_answer, dict) else {})},
        )
        return rec

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

        nonlocal evidence_window

        if not q:
            return None, q

        tdb_ctx_aa = _build_decide_context(hands_last_message=q, recent_evidence=evidence_window)
        tdb_ctx_aa_obj = tdb_ctx_aa.to_prompt_obj()
        aa_prompt = auto_answer_to_hands_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_aa_obj,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            recent_evidence=evidence_window,
            hands_last_message=q,
        )
        aa_obj, aa_ref, aa_state = _mind_call(
            schema_filename="auto_answer_to_hands.json",
            prompt=aa_prompt,
            tag=f"{tag_suffix}_b{batch_idx}",
            batch_id=f"b{batch_idx}.{batch_suffix}",
        )

        aa_out = _empty_auto_answer()
        if aa_obj is None:
            if aa_state == "skipped":
                aa_out["notes"] = note_skipped
            else:
                aa_out["notes"] = note_error
        else:
            aa_out = aa_obj

        _append_auto_answer_record(
            batch_id=f"b{batch_idx}.{batch_suffix}",
            mind_transcript_ref=aa_ref,
            auto_answer=aa_out if isinstance(aa_out, dict) else {},
        )

        aa_text = ""
        if isinstance(aa_out, dict) and bool(aa_out.get("should_answer", False)):
            aa_text = str(aa_out.get("hands_answer_input") or "").strip()
        chk_text = _get_check_input(checks_obj if isinstance(checks_obj, dict) else None)
        combined = join_hands_inputs(aa_text, chk_text)
        if combined:
            queued = _queue_next_input(
                nxt=combined,
                hands_last_message=hands_last,
                batch_id=f"b{batch_idx}.{batch_suffix}",
                reason=queue_reason,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                thought_db_context=tdb_ctx_obj,
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            )
            return (True if queued else False), q

        if isinstance(aa_out, dict) and bool(aa_out.get("needs_user_input", False)):
            q2 = str(aa_out.get("ask_user_question") or "").strip()
            if q2:
                q = q2
        return None, q

    def _ask_user_redecide_with_input(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        checks_obj: dict[str, Any],
        answer: str,
    ) -> bool:
        """Re-decide after collecting user input (no extra Hands run before decision)."""

        nonlocal status, notes, last_decide_next_rec, evidence_window

        tdb_ctx2 = _build_decide_context(hands_last_message=hands_last, recent_evidence=evidence_window)
        tdb_ctx2_obj = tdb_ctx2.to_prompt_obj()
        tdb_ctx2_summary = summarize_thought_db_context(tdb_ctx2)
        decision_prompt2 = decide_next_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx2_obj,
            active_workflow=load_active_workflow(workflow_run=workflow_run, load_effective=wf_registry.load_effective),
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            recent_evidence=evidence_window,
            hands_last_message=hands_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer=_empty_auto_answer(),
        )
        decision_obj2, decision2_mind_ref, decision2_state = _mind_call(
            schema_filename="decide_next.json",
            prompt=decision_prompt2,
            tag=f"decide_after_user_b{batch_idx}",
            batch_id=f"b{batch_idx}.after_user",
        )
        if decision_obj2 is None:
            chk_text2 = _get_check_input(checks_obj if isinstance(checks_obj, dict) else None)
            combined_user2 = join_hands_inputs(answer.strip(), chk_text2)
            if not _queue_next_input(
                nxt=combined_user2,
                hands_last_message=hands_last,
                batch_id=f"b{batch_idx}.after_user",
                reason=(
                    "mind_circuit_open(decide_next after user): send user answer"
                    if decision2_state == "skipped"
                    else "mind_error(decide_next after user): send user answer"
                ),
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                thought_db_context=tdb_ctx2_obj,
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            ):
                return False
            return True

        decision_obj_after_user = decision_obj2
        decide_rec2 = _log_decide_next(
            decision_obj=decision_obj_after_user,
            batch_id=f"b{batch_idx}",
            phase="after_user",
            mind_transcript_ref=decision2_mind_ref,
            thought_db_context_summary=tdb_ctx2_summary,
        )
        if isinstance(decide_rec2, dict) and str(decide_rec2.get("event_id") or "").strip():
            last_decide_next_rec = decide_rec2
        if decide_rec2:
            segment_add_and_persist(segment_add=_segment_add, persist_segment_state=_persist_segment_state, item=decide_rec2)

        overlay_update = decision_obj_after_user.get("update_project_overlay") or {}
        if isinstance(overlay_update, dict):
            _apply_set_testless_strategy_overlay_update(
                set_tls=overlay_update.get("set_testless_strategy"),
                decide_event_id=str((decide_rec2 or {}).get("event_id") or ""),
                fallback_batch_id=f"b{batch_idx}.after_user.set_testless",
                default_rationale="decide_next(after_user) overlay update",
                source="decide_next.after_user:set_testless_strategy",
            )

        _handle_learn_suggested(
            learn_suggested=decision_obj_after_user.get("learn_suggested"),
            batch_id=f"b{batch_idx}.after_user",
            source="decide_next.after_user",
            mind_transcript_ref=decision2_mind_ref,
            source_event_ids=[str((decide_rec2 or {}).get("event_id") or "").strip()],
        )

        next_action = str(decision_obj_after_user.get("next_action") or "stop")
        status = str(decision_obj_after_user.get("status") or "not_done")
        notes = str(decision_obj_after_user.get("notes") or "")

        if next_action == "stop":
            return False
        if next_action == "send_to_hands":
            nxt = str(decision_obj_after_user.get("next_hands_input") or "").strip()
            if not nxt:
                status = "blocked"
                notes = "decide_next returned send_to_hands without next_hands_input (after user input)"
                return False
            if not _queue_next_input(
                nxt=nxt,
                hands_last_message=hands_last,
                batch_id=f"b{batch_idx}.after_user",
                reason="send_to_hands after user input",
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                thought_db_context=tdb_ctx2_obj,
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            ):
                return False
            return True

        status = "blocked"
        notes = f"unexpected next_action={next_action} after user input"
        return False

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

        q = str(decision_obj.get("ask_user_question") or "Need more information:").strip()

        r1, q = _ask_user_auto_answer_attempt(
            batch_idx=batch_idx,
            q=q,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj,
            batch_suffix="from_decide",
            tag_suffix="autoanswer_from_decide",
            queue_reason="auto-answered instead of prompting user",
            note_skipped="skipped: mind_circuit_open (auto_answer_to_hands from decide_next)",
            note_error="mind_error: auto_answer_to_hands(from decide_next) failed; see EvidenceLog kind=mind_error",
        )
        if isinstance(r1, bool):
            return r1

        # Before asking the user, do a conservative cross-project recall and retry auto_answer once.
        _maybe_cross_project_recall(
            batch_id=f"b{batch_idx}.from_decide.before_user_recall",
            reason="before_ask_user",
            query=(q + "\n" + task).strip(),
        )
        r2, q = _ask_user_auto_answer_attempt(
            batch_idx=batch_idx,
            q=q,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj,
            batch_suffix="from_decide.after_recall",
            tag_suffix="autoanswer_from_decide_after_recall",
            queue_reason="auto-answered (after recall) instead of prompting user",
            note_skipped="skipped: mind_circuit_open (auto_answer_to_hands from decide_next after recall)",
            note_error="mind_error: auto_answer_to_hands(from decide_next after recall) failed; see EvidenceLog kind=mind_error",
        )
        if isinstance(r2, bool):
            return r2

        answer = _read_user_answer(q or "Need more information:")
        if not answer:
            status = "blocked"
            notes = "user did not provide required input"
            return False
        _append_user_input_record(batch_id=f"b{batch_idx}", question=q, answer=answer)

        return _ask_user_redecide_with_input(
            batch_idx=batch_idx,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            answer=answer,
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

        nonlocal evidence_window

        tdb_ctx = _build_decide_context(hands_last_message=hands_last, recent_evidence=evidence_window)
        tdb_ctx_obj = tdb_ctx.to_prompt_obj()
        tdb_ctx_summary = summarize_thought_db_context(tdb_ctx)
        decision_prompt = decide_next_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_obj,
            active_workflow=load_active_workflow(workflow_run=workflow_run, load_effective=wf_registry.load_effective),
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            recent_evidence=evidence_window,
            hands_last_message=hands_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer=auto_answer_obj,
        )
        decision_obj, decision_mind_ref, decision_state = _mind_call(
            schema_filename="decide_next.json",
            prompt=decision_prompt,
            tag=f"decide_b{batch_idx}",
            batch_id=batch_id,
        )
        return (
            decision_obj if isinstance(decision_obj, dict) else None,
            str(decision_mind_ref or ""),
            str(decision_state or ""),
            tdb_ctx_obj,
            tdb_ctx_summary,
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

        decide_rec = _log_decide_next(
            decision_obj=decision_obj,
            batch_id=f"b{batch_idx}",
            phase="initial",
            mind_transcript_ref=decision_mind_ref,
            thought_db_context_summary=tdb_ctx_summary,
        )
        if isinstance(decide_rec, dict) and str(decide_rec.get("event_id") or "").strip():
            last_decide_next_rec = decide_rec
        if decide_rec:
            _segment_add(decide_rec)
        else:
            _segment_add(
                {
                    "kind": "decide_next",
                    "batch_id": f"b{batch_idx}",
                    "next_action": decision_obj.get("next_action"),
                    "status": decision_obj.get("status"),
                    "notes": decision_obj.get("notes"),
                }
            )
        _persist_segment_state()

        # Apply project overlay updates (e.g., testless verification strategy).
        overlay_update = decision_obj.get("update_project_overlay") or {}
        if isinstance(overlay_update, dict):
            _apply_set_testless_strategy_overlay_update(
                set_tls=overlay_update.get("set_testless_strategy"),
                decide_event_id=str((decide_rec or {}).get("event_id") or ""),
                fallback_batch_id=f"b{batch_idx}.set_testless",
                default_rationale="decide_next overlay update",
                source="decide_next:set_testless_strategy",
            )

        # Write learn suggestions (append-only; reversible via claim retraction).
        _handle_learn_suggested(
            learn_suggested=decision_obj.get("learn_suggested"),
            batch_id=f"b{batch_idx}",
            source="decide_next",
            mind_transcript_ref=decision_mind_ref,
            source_event_ids=[str((decide_rec or {}).get("event_id") or "").strip()],
        )

        next_action = str(decision_obj.get("next_action") or "stop")
        status = str(decision_obj.get("status") or "not_done")
        notes = str(decision_obj.get("notes") or "")
        cf = decision_obj.get("confidence")
        try:
            cf_s = f"{float(cf):.2f}" if cf is not None else ""
        except Exception:
            cf_s = str(cf or "")
        _emit_prefixed(
            "[mi]",
            "decide_next "
            + f"status={status} next_action={next_action} "
            + (f"confidence={cf_s}" if cf_s else ""),
        )
        return next_action, decide_rec if isinstance(decide_rec, dict) else None

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

        nonlocal evidence_window

        q = str(question or "").strip()
        _maybe_cross_project_recall(
            batch_id=f"b{batch_idx}.before_user_recall",
            reason="before_ask_user",
            query=(q + "\n" + task).strip(),
        )
        aa_prompt_retry = auto_answer_to_hands_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_batch_obj,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            recent_evidence=evidence_window,
            hands_last_message=q,
        )
        aa_obj_r, aa_r_ref, aa_r_state = _mind_call(
            schema_filename="auto_answer_to_hands.json",
            prompt=aa_prompt_retry,
            tag=f"autoanswer_retry_after_recall_b{batch_idx}",
            batch_id=f"b{batch_idx}.after_recall",
        )
        if aa_obj_r is None:
            aa_retry = _empty_auto_answer()
            if aa_r_state == "skipped":
                aa_retry["notes"] = "skipped: mind_circuit_open (auto_answer_to_hands retry after recall)"
            else:
                aa_retry["notes"] = "mind_error: auto_answer_to_hands retry failed; see EvidenceLog kind=mind_error"
        else:
            aa_retry = aa_obj_r if isinstance(aa_obj_r, dict) else _empty_auto_answer()
        _append_auto_answer_record(
            batch_id=f"b{batch_idx}.after_recall",
            mind_transcript_ref=aa_r_ref,
            auto_answer=aa_retry if isinstance(aa_retry, dict) else {},
        )
        if isinstance(aa_retry, dict) and bool(aa_retry.get("needs_user_input", False)):
            q2 = str(aa_retry.get("ask_user_question") or "").strip()
            if q2:
                q = q2
        return aa_retry if isinstance(aa_retry, dict) else _empty_auto_answer(), q

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

        check_text = _get_check_input(checks_obj if isinstance(checks_obj, dict) else None)
        combined = join_hands_inputs(str(answer_text or "").strip(), check_text)
        if not combined:
            return None
        if not _queue_next_input(
            nxt=combined,
            hands_last_message=hands_last,
            batch_id=batch_id,
            reason=queue_reason,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            thought_db_context=tdb_ctx_batch_obj,
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        ):
            return False
        return True

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

        nonlocal status, notes

        answer = _read_user_answer(question)
        if not answer:
            status = "blocked"
            notes = "user did not provide required input"
            return False
        _append_user_input_record(batch_id=f"b{batch_idx}", question=question, answer=answer)

        queued = _predecide_try_queue_answer_with_checks(
            batch_idx=batch_idx,
            batch_id=f"b{batch_idx}",
            queue_reason="answered after user input",
            answer_text=answer,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj,
        )
        return bool(queued)

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

        q = str(auto_answer_obj.get("ask_user_question") or "").strip() or hands_last.strip() or "Need more information:"
        aa_retry, q = _predecide_retry_auto_answer_after_recall(
            batch_idx=batch_idx,
            question=q,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj,
        )

        aa_text = ""
        if isinstance(aa_retry, dict) and bool(aa_retry.get("should_answer", False)):
            aa_text = str(aa_retry.get("hands_answer_input") or "").strip()
        queued_retry = _predecide_try_queue_answer_with_checks(
            batch_idx=batch_idx,
            batch_id=f"b{batch_idx}.after_recall",
            queue_reason="auto-answered after cross-project recall",
            answer_text=aa_text,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj,
        )
        if isinstance(queued_retry, bool):
            return queued_retry, checks_obj if isinstance(checks_obj, dict) else {}

        asked = _predecide_prompt_user_then_queue(
            batch_idx=batch_idx,
            question=q,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj,
        )
        return asked, checks_obj if isinstance(checks_obj, dict) else {}

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
        evidence_item = {
            "kind": "evidence",
            "batch_id": batch_id,
            "ts": now_rfc3339(),
            "thread_id": thread_id,
            "hands_transcript_ref": str(ctx.hands_transcript),
            "mind_transcript_ref": evidence_mind_ref,
            "mi_input": ctx.batch_input,
            "transcript_observation": summary.get("transcript_observation") or {},
            "repo_observation": repo_obs,
            **evidence_obj,
        }
        evidence_rec = evw.append(evidence_item)
        last_evidence_rec = evidence_rec
        append_evidence_window(evidence_window, evidence_rec)
        segment_add_and_persist(segment_add=_segment_add, persist_segment_state=_persist_segment_state, item=evidence_rec)

        hands_last = result.last_agent_message()
        tdb_ctx_batch = _build_decide_context(hands_last_message=hands_last, recent_evidence=evidence_window)
        tdb_ctx_batch_obj = tdb_ctx_batch.to_prompt_obj()
        return (
            summary if isinstance(summary, dict) else {},
            evidence_obj if isinstance(evidence_obj, dict) else {},
            str(hands_last or ""),
            tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        )

    def _workflow_progress_build_latest_evidence(
        *,
        batch_id: str,
        summary: dict[str, Any],
        evidence_obj: dict[str, Any],
        repo_obs: dict[str, Any],
    ) -> dict[str, Any]:
        """Build workflow_progress latest_evidence payload."""

        return {
            "batch_id": batch_id,
            "facts": evidence_obj.get("facts") if isinstance(evidence_obj, dict) else [],
            "actions": evidence_obj.get("actions") if isinstance(evidence_obj, dict) else [],
            "results": evidence_obj.get("results") if isinstance(evidence_obj, dict) else [],
            "unknowns": evidence_obj.get("unknowns") if isinstance(evidence_obj, dict) else [],
            "risk_signals": evidence_obj.get("risk_signals") if isinstance(evidence_obj, dict) else [],
            "repo_observation": repo_obs if isinstance(repo_obs, dict) else {},
            "transcript_observation": (summary if isinstance(summary, dict) else {}).get("transcript_observation") or {},
        }

    def _workflow_progress_query(
        *,
        batch_idx: int,
        batch_id: str,
        active_wf: dict[str, Any],
        latest_evidence: dict[str, Any],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
        ctx: BatchExecutionContext,
    ) -> tuple[dict[str, Any], str, str]:
        """Query Mind for workflow progress state."""

        wf_prog_prompt = workflow_progress_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_batch_obj,
            workflow=active_wf if isinstance(active_wf, dict) else {},
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            latest_evidence=latest_evidence if isinstance(latest_evidence, dict) else {},
            last_batch_input=ctx.batch_input,
            hands_last_message=hands_last,
        )
        wf_prog_obj, wf_prog_ref, wf_prog_state = _mind_call(
            schema_filename="workflow_progress.json",
            prompt=wf_prog_prompt,
            tag=f"wf_progress_b{batch_idx}",
            batch_id=f"{batch_id}.workflow_progress",
        )
        return (
            wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
            str(wf_prog_ref or ""),
            str(wf_prog_state or ""),
        )

    def _workflow_progress_record_event(
        *,
        batch_id: str,
        active_wf: dict[str, Any],
        wf_prog_obj: dict[str, Any],
        wf_prog_ref: str,
        wf_prog_state: str,
    ) -> None:
        """Record workflow_progress event in evidence log."""

        evw.append(
            {
                "kind": "workflow_progress",
                "batch_id": f"{batch_id}.workflow_progress",
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "workflow_id": str((active_wf if isinstance(active_wf, dict) else {}).get("id") or ""),
                "workflow_name": str((active_wf if isinstance(active_wf, dict) else {}).get("name") or ""),
                "state": str(wf_prog_state or ""),
                "mind_transcript_ref": str(wf_prog_ref or ""),
                "output": wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
            }
        )

    def _workflow_progress_apply_overlay_if_changed(
        *,
        batch_id: str,
        active_wf: dict[str, Any],
        wf_prog_obj: dict[str, Any],
    ) -> None:
        """Apply workflow progress output and persist overlay only when changed."""

        if apply_workflow_progress_output(
            active_workflow=active_wf if isinstance(active_wf, dict) else {},
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            wf_progress_output=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
            batch_id=batch_id,
            thread_id=str(thread_id or ""),
            now_ts=now_rfc3339,
        ):
            overlay["workflow_run"] = workflow_run
            write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)

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

        latest_evidence = _workflow_progress_build_latest_evidence(
            batch_id=batch_id,
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        )
        wf_prog_obj, wf_prog_ref, wf_prog_state = _workflow_progress_query(
            batch_idx=batch_idx,
            batch_id=batch_id,
            active_wf=active_wf,
            latest_evidence=latest_evidence,
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            ctx=ctx,
        )
        _workflow_progress_record_event(
            batch_id=batch_id,
            active_wf=active_wf,
            wf_prog_obj=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
            wf_prog_ref=wf_prog_ref,
            wf_prog_state=wf_prog_state,
        )
        _workflow_progress_apply_overlay_if_changed(
            batch_id=batch_id,
            active_wf=active_wf,
            wf_prog_obj=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
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

        nonlocal evidence_window

        risk_rec = evw.append(
            {
                "kind": "risk_event",
                "batch_id": f"b{batch_idx}",
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "risk_signals": risk_signals,
                "mind_transcript_ref": risk_mind_ref,
                "risk": risk_obj if isinstance(risk_obj, dict) else {},
            }
        )
        append_evidence_window(
            evidence_window,
            {
                "kind": "risk_event",
                "batch_id": f"b{batch_idx}",
                "event_id": risk_rec.get("event_id"),
                **(risk_obj if isinstance(risk_obj, dict) else {}),
            },
        )
        segment_add_and_persist(
            segment_add=_segment_add,
            persist_segment_state=_persist_segment_state,
            item={
                "kind": "risk_event",
                "batch_id": f"b{batch_idx}",
                "event_id": risk_rec.get("event_id"),
                "risk_signals": risk_signals,
                "category": (risk_obj if isinstance(risk_obj, dict) else {}).get("category"),
                "severity": (risk_obj if isinstance(risk_obj, dict) else {}).get("severity"),
            },
        )
        return risk_rec if isinstance(risk_rec, dict) else {}

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

        nonlocal evidence_window

        aa_prompt = auto_answer_to_hands_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=_mindspec_base_runtime(),
            project_overlay=overlay,
            thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            recent_evidence=evidence_window,
            hands_last_message=hands_last,
        )
        aa_obj, auto_answer_mind_ref, aa_state = _mind_call(
            schema_filename="auto_answer_to_hands.json",
            prompt=aa_prompt,
            tag=f"autoanswer_b{batch_idx}",
            batch_id=batch_id,
        )
        if aa_obj is None:
            auto_answer_obj = _empty_auto_answer()
            if aa_state == "skipped":
                auto_answer_obj["notes"] = "skipped: mind_circuit_open (auto_answer_to_hands)"
            else:
                auto_answer_obj["notes"] = "mind_error: auto_answer_to_hands failed; see EvidenceLog kind=mind_error"
        else:
            auto_answer_obj = aa_obj if isinstance(aa_obj, dict) else _empty_auto_answer()
        return (
            auto_answer_obj if isinstance(auto_answer_obj, dict) else _empty_auto_answer(),
            str(auto_answer_mind_ref or ""),
            str(aa_state or ""),
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
        batch_id = f"b{batch_idx}"
        batch_ts = now_rfc3339().replace(":", "").replace("-", "")
        light = build_light_injection(tdb=tdb, as_of_ts=now_rfc3339())
        batch_input = next_input.strip()
        hands_prompt = light + "\n" + batch_input + "\n"
        sent_ts = now_rfc3339()
        prompt_sha256 = hashlib.sha256(hands_prompt.encode("utf-8")).hexdigest()
        use_resume = thread_id is not None and hands_resume is not None and thread_id != "unknown"
        attempted_overlay_resume = bool(use_resume and resumed_from_overlay and batch_idx == 0)
        return BatchExecutionContext(
            batch_idx=batch_idx,
            batch_id=batch_id,
            batch_ts=batch_ts,
            hands_transcript=project_paths.transcripts_dir / "hands" / f"{batch_ts}_b{batch_idx}.jsonl",
            batch_input=batch_input,
            hands_prompt=hands_prompt,
            light_injection=light,
            sent_ts=sent_ts,
            prompt_sha256=prompt_sha256,
            use_resume=use_resume,
            attempted_overlay_resume=attempted_overlay_resume,
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
