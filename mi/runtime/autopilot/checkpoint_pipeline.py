from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CheckpointPipelineDeps:
    build_decide_context: Callable[..., Any]
    checkpoint_decide_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    evidence_append: Callable[[dict[str, Any]], Any]
    mine_workflow_from_segment: Callable[..., None]
    mine_preferences_from_segment: Callable[..., None]
    mine_claims_from_segment: Callable[..., None]
    materialize_snapshot: Callable[..., Any]
    materialize_nodes_from_checkpoint: Callable[..., None]
    new_segment_state: Callable[..., dict[str, Any]]
    now_ts: Callable[[], str]
    truncate: Callable[[str, int], str]


@dataclass(frozen=True)
class CheckpointPipelineResult:
    segment_state: dict[str, Any]
    segment_records: list[dict[str, Any]]
    last_checkpoint_key: str
    persist_segment_state: bool


def run_checkpoint_pipeline(
    *,
    checkpoint_enabled: bool,
    segment_state: dict[str, Any],
    segment_records: list[dict[str, Any]],
    last_checkpoint_key: str,
    batch_id: str,
    planned_next_input: str,
    status_hint: str,
    note: str,
    thread_id: str,
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    evidence_window: list[dict[str, Any]],
    deps: CheckpointPipelineDeps,
) -> CheckpointPipelineResult:
    """Run checkpoint decision + optional mining/materialization (behavior-preserving)."""

    if not checkpoint_enabled:
        return CheckpointPipelineResult(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=segment_records if isinstance(segment_records, list) else [],
            last_checkpoint_key=str(last_checkpoint_key or ""),
            persist_segment_state=False,
        )
    if not isinstance(segment_records, list):
        return CheckpointPipelineResult(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=[],
            last_checkpoint_key=str(last_checkpoint_key or ""),
            persist_segment_state=False,
        )

    base_bid = str(batch_id or "").split(".", 1)[0].strip()
    if not base_bid:
        return CheckpointPipelineResult(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=segment_records,
            last_checkpoint_key=str(last_checkpoint_key or ""),
            persist_segment_state=False,
        )

    key = base_bid + ":" + str(status_hint or "").strip()
    if key == str(last_checkpoint_key or ""):
        return CheckpointPipelineResult(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=segment_records,
            last_checkpoint_key=str(last_checkpoint_key or ""),
            persist_segment_state=False,
        )

    new_last_key = key
    if isinstance(segment_state, dict):
        cur_tid = str(thread_id or "").strip()
        if cur_tid and cur_tid != "unknown":
            segment_state["thread_id"] = cur_tid
        segment_state["task_hint"] = deps.truncate(str(task or "").strip(), 200)

    tdb_ctx = deps.build_decide_context(hands_last_message="", recent_evidence=segment_records[-8:])
    tdb_ctx_obj = tdb_ctx.to_prompt_obj()
    prompt = deps.checkpoint_decide_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        segment_evidence=segment_records,
        current_batch_id=base_bid,
        planned_next_input=deps.truncate(planned_next_input or "", 2000),
        status_hint=str(status_hint or ""),
        notes=(note or "").strip(),
    )
    out, mind_ref, state = deps.mind_call(
        schema_filename="checkpoint_decide.json",
        prompt=prompt,
        tag=f"checkpoint:{base_bid}",
        batch_id=f"{base_bid}.checkpoint",
    )

    deps.evidence_append(
        {
            "kind": "checkpoint",
            "batch_id": f"{base_bid}.checkpoint",
            "ts": deps.now_ts(),
            "thread_id": thread_id or "",
            "segment_id": str((segment_state if isinstance(segment_state, dict) else {}).get("segment_id") or ""),
            "state": state,
            "mind_transcript_ref": mind_ref,
            "planned_next_input": deps.truncate(planned_next_input or "", 800),
            "status_hint": str(status_hint or ""),
            "note": (note or "").strip(),
            "output": out if isinstance(out, dict) else {},
        }
    )

    if not isinstance(out, dict):
        return CheckpointPipelineResult(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=segment_records,
            last_checkpoint_key=new_last_key,
            persist_segment_state=True,
        )

    if not bool(out.get("should_checkpoint", False)):
        return CheckpointPipelineResult(
            segment_state=segment_state if isinstance(segment_state, dict) else {},
            segment_records=segment_records,
            last_checkpoint_key=new_last_key,
            persist_segment_state=True,
        )

    if bool(out.get("should_mine_workflow", False)):
        deps.mine_workflow_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")
    if bool(out.get("should_mine_preferences", False)):
        deps.mine_preferences_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")
    deps.mine_claims_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")

    snap = deps.materialize_snapshot(
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
        snap_evidence = getattr(snap, "evidence_event", None)
        rec = deps.evidence_append(snap_evidence if isinstance(snap_evidence, dict) else {})
        snap_rec = rec if isinstance(rec, dict) else None
        snap_window = getattr(snap, "window_entry", None)
        win = dict(snap_window) if isinstance(snap_window, dict) else {}
        if isinstance((rec if isinstance(rec, dict) else {}).get("event_id"), str) and rec.get("event_id"):
            win["event_id"] = rec["event_id"]
        if win:
            evidence_window.append(win)
            evidence_window[:] = evidence_window[-8:]

    deps.materialize_nodes_from_checkpoint(
        seg_evidence=segment_records,
        snapshot_rec=snap_rec,
        base_batch_id=base_bid,
        checkpoint_kind=str(out.get("checkpoint_kind") or ""),
        status_hint=str(status_hint or ""),
        planned_next_input=str(planned_next_input or ""),
        note=(note or "").strip(),
    )

    new_state = deps.new_segment_state(reason=f"checkpoint:{out.get('checkpoint_kind')}", thread_hint=str(thread_id or ""))
    new_records = new_state.get("records") if isinstance(new_state.get("records"), list) else []
    new_state["records"] = new_records
    return CheckpointPipelineResult(
        segment_state=new_state if isinstance(new_state, dict) else {},
        segment_records=new_records if isinstance(new_records, list) else [],
        last_checkpoint_key=new_last_key,
        persist_segment_state=True,
    )
