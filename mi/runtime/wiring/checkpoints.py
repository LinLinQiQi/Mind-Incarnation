from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.checkpoint_pipeline import CheckpointPipelineDeps, CheckpointPipelineResult, run_checkpoint_pipeline


@dataclass(frozen=True)
class CheckpointWiringDeps:
    """Wiring bundle for checkpoint pipeline execution (internal)."""

    checkpoint_enabled: bool
    task: str
    hands_provider: str
    mindspec_base: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    evidence_window: list[dict[str, Any]]
    thread_id_getter: Callable[[], str]
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


def run_checkpoint_pipeline_wired(
    *,
    segment_state: dict[str, Any],
    segment_records: list[dict[str, Any]],
    last_checkpoint_key: str,
    batch_id: str,
    planned_next_input: str,
    status_hint: str,
    note: str,
    deps: CheckpointWiringDeps,
) -> CheckpointPipelineResult:
    """Run checkpoint pipeline with runner wiring (behavior-preserving)."""

    return run_checkpoint_pipeline(
        checkpoint_enabled=bool(deps.checkpoint_enabled),
        segment_state=segment_state if isinstance(segment_state, dict) else {},
        segment_records=segment_records if isinstance(segment_records, list) else [],
        last_checkpoint_key=str(last_checkpoint_key or ""),
        batch_id=str(batch_id or ""),
        planned_next_input=str(planned_next_input or ""),
        status_hint=str(status_hint or ""),
        note=str(note or ""),
        thread_id=str(deps.thread_id_getter() or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        mindspec_base=deps.mindspec_base() if callable(deps.mindspec_base) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        evidence_window=deps.evidence_window if isinstance(deps.evidence_window, list) else [],
        deps=CheckpointPipelineDeps(
            build_decide_context=deps.build_decide_context,
            checkpoint_decide_prompt_builder=deps.checkpoint_decide_prompt_builder,
            mind_call=deps.mind_call,
            evidence_append=deps.evidence_append,
            mine_workflow_from_segment=deps.mine_workflow_from_segment,
            mine_preferences_from_segment=deps.mine_preferences_from_segment,
            mine_claims_from_segment=deps.mine_claims_from_segment,
            materialize_snapshot=deps.materialize_snapshot,
            materialize_nodes_from_checkpoint=deps.materialize_nodes_from_checkpoint,
            new_segment_state=deps.new_segment_state,
            now_ts=deps.now_ts,
            truncate=deps.truncate,
        ),
    )

