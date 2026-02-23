from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DecideNextQueryDeps:
    """Dependencies for decide_next prompt/query flow."""

    build_decide_context: Callable[..., Any]
    summarize_thought_db_context: Callable[[Any], dict[str, Any]]
    decide_next_prompt_builder: Callable[..., str]
    load_active_workflow: Callable[..., Any]
    mind_call: Callable[..., tuple[Any, str, str]]


def query_decide_next(
    *,
    batch_idx: int,
    batch_id: str,
    task: str,
    hands_provider: str,
    runtime_cfg: dict[str, Any],
    project_overlay: dict[str, Any],
    workflow_run: dict[str, Any],
    workflow_load_effective: Callable[[], list[dict[str, Any]]],
    recent_evidence: list[dict[str, Any]],
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    auto_answer_obj: dict[str, Any],
    deps: DecideNextQueryDeps,
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any], dict[str, Any]]:
    """Build decide_next prompt, query Mind, and return decision + context summaries."""

    tdb_ctx = deps.build_decide_context(
        hands_last_message=str(hands_last or ""),
        recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
    )
    tdb_ctx_obj = tdb_ctx.to_prompt_obj() if hasattr(tdb_ctx, "to_prompt_obj") else {}
    if not isinstance(tdb_ctx_obj, dict):
        tdb_ctx_obj = {}
    tdb_ctx_summary = deps.summarize_thought_db_context(tdb_ctx)

    decision_prompt = deps.decide_next_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        active_workflow=deps.load_active_workflow(
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            load_effective=workflow_load_effective,
        ),
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
        hands_last_message=str(hands_last or ""),
        repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        auto_answer=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
    )
    decision_obj, decision_mind_ref, decision_state = deps.mind_call(
        schema_filename="decide_next.json",
        prompt=decision_prompt,
        tag=f"decide_b{batch_idx}",
        batch_id=batch_id,
    )
    return (
        decision_obj if isinstance(decision_obj, dict) else None,
        str(decision_mind_ref or ""),
        str(decision_state or ""),
        tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        tdb_ctx_summary if isinstance(tdb_ctx_summary, dict) else {},
    )


@dataclass(frozen=True)
class DecideRecordEffectsDeps:
    """Dependencies for decide_next side-effect recording."""

    log_decide_next: Callable[..., dict[str, Any] | None]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    apply_set_testless_strategy_overlay_update: Callable[..., None]
    handle_learn_suggested: Callable[..., None]
    emit_prefixed: Callable[[str, str], None]


@dataclass(frozen=True)
class DecideRecordEffectsResult:
    """Normalized outputs from decide_next effect handling."""

    next_action: str
    status: str
    notes: str
    decide_rec: dict[str, Any] | None


def record_decide_next_effects(
    *,
    batch_idx: int,
    decision_obj: dict[str, Any],
    decision_mind_ref: str,
    tdb_ctx_summary: dict[str, Any],
    deps: DecideRecordEffectsDeps,
) -> DecideRecordEffectsResult:
    """Persist decide_next outputs and apply declared side effects."""

    decide_rec = deps.log_decide_next(
        decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
        batch_id=f"b{batch_idx}",
        phase="initial",
        mind_transcript_ref=str(decision_mind_ref or ""),
        thought_db_context_summary=tdb_ctx_summary if isinstance(tdb_ctx_summary, dict) else {},
    )
    if decide_rec:
        deps.segment_add(decide_rec if isinstance(decide_rec, dict) else {})
    else:
        deps.segment_add(
            {
                "kind": "decide_next",
                "batch_id": f"b{batch_idx}",
                "next_action": decision_obj.get("next_action"),
                "status": decision_obj.get("status"),
                "notes": decision_obj.get("notes"),
            }
        )
    deps.persist_segment_state()

    overlay_update = decision_obj.get("update_project_overlay") or {}
    if isinstance(overlay_update, dict):
        deps.apply_set_testless_strategy_overlay_update(
            set_tls=overlay_update.get("set_testless_strategy"),
            decide_event_id=str((decide_rec or {}).get("event_id") or ""),
            fallback_batch_id=f"b{batch_idx}.set_testless",
            default_rationale="decide_next overlay update",
            source="decide_next:set_testless_strategy",
        )

    deps.handle_learn_suggested(
        learn_suggested=decision_obj.get("learn_suggested"),
        batch_id=f"b{batch_idx}",
        source="decide_next",
        mind_transcript_ref=str(decision_mind_ref or ""),
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
    deps.emit_prefixed(
        "[mi]",
        "decide_next "
        + f"status={status} next_action={next_action} "
        + (f"confidence={cf_s}" if cf_s else ""),
    )

    return DecideRecordEffectsResult(
        next_action=next_action,
        status=status,
        notes=notes,
        decide_rec=decide_rec if isinstance(decide_rec, dict) else None,
    )
