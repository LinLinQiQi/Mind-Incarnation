from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.workflow_progress_flow import (
    WorkflowProgressQueryDeps,
    apply_workflow_progress_and_persist,
    append_workflow_progress_event,
    build_workflow_progress_latest_evidence,
    query_workflow_progress,
)


@dataclass(frozen=True)
class WorkflowProgressWiringDeps:
    """Wiring bundle for workflow_progress prompt/query/apply flow."""

    task: str
    hands_provider: str
    runtime_cfg_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    workflow_run: dict[str, Any]
    workflow_load_effective: Callable[..., Any]
    load_active_workflow: Callable[..., Any]

    workflow_progress_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]

    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]
    thread_id_getter: Callable[[], str | None]

    apply_workflow_progress_output_fn: Callable[..., bool]
    write_project_overlay: Callable[[dict[str, Any]], None]


def apply_workflow_progress_wired(
    *,
    batch_idx: int,
    batch_id: str,
    summary: dict[str, Any],
    evidence_obj: dict[str, Any],
    repo_obs: dict[str, Any],
    hands_last: str,
    tdb_ctx_batch_obj: dict[str, Any],
    last_batch_input: str,
    deps: WorkflowProgressWiringDeps,
) -> None:
    """Apply workflow_progress using runner wiring (behavior-preserving)."""

    active_wf = deps.load_active_workflow(
        workflow_run=deps.workflow_run if isinstance(deps.workflow_run, dict) else {},
        load_effective=deps.workflow_load_effective,
    )
    if not (isinstance(active_wf, dict) and active_wf):
        return

    latest_evidence = build_workflow_progress_latest_evidence(
        batch_id=str(batch_id or ""),
        summary=summary if isinstance(summary, dict) else {},
        evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
    )

    wf_prog_obj, wf_prog_ref, wf_prog_state = query_workflow_progress(
        batch_idx=int(batch_idx),
        batch_id=str(batch_id or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        runtime_cfg=deps.runtime_cfg_getter() if callable(deps.runtime_cfg_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        active_workflow=active_wf,
        workflow_run=deps.workflow_run if isinstance(deps.workflow_run, dict) else {},
        latest_evidence=latest_evidence,
        last_batch_input=str(last_batch_input or ""),
        hands_last_message=str(hands_last or ""),
        thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        deps=WorkflowProgressQueryDeps(
            workflow_progress_prompt_builder=deps.workflow_progress_prompt_builder,
            mind_call=deps.mind_call,
        ),
    )

    thread_id = deps.thread_id_getter() if callable(deps.thread_id_getter) else None
    append_workflow_progress_event(
        batch_id=str(batch_id or ""),
        thread_id=str(thread_id or ""),
        active_workflow=active_wf,
        wf_prog_obj=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
        wf_prog_ref=str(wf_prog_ref or ""),
        wf_prog_state=str(wf_prog_state or ""),
        evidence_append=deps.evidence_append,
        now_ts=deps.now_ts,
    )

    def _persist_overlay() -> None:
        if isinstance(deps.project_overlay, dict):
            deps.project_overlay["workflow_run"] = deps.workflow_run
            deps.write_project_overlay(deps.project_overlay)

    apply_workflow_progress_and_persist(
        batch_id=str(batch_id or ""),
        thread_id=str(thread_id or ""),
        active_workflow=active_wf,
        workflow_run=deps.workflow_run if isinstance(deps.workflow_run, dict) else {},
        wf_prog_obj=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
        apply_workflow_progress_output_fn=deps.apply_workflow_progress_output_fn,
        persist_overlay=_persist_overlay,
        now_ts=deps.now_ts,
    )

