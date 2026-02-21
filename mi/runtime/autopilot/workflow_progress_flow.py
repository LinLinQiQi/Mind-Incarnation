from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkflowProgressQueryDeps:
    """Dependencies for workflow_progress prompt/query orchestration."""

    workflow_progress_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]


def build_workflow_progress_latest_evidence(
    *,
    batch_id: str,
    summary: dict[str, Any],
    evidence_obj: dict[str, Any],
    repo_obs: dict[str, Any],
) -> dict[str, Any]:
    """Build normalized latest_evidence payload for workflow_progress."""

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


def query_workflow_progress(
    *,
    batch_idx: int,
    batch_id: str,
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    active_workflow: dict[str, Any],
    workflow_run: dict[str, Any],
    latest_evidence: dict[str, Any],
    last_batch_input: str,
    hands_last_message: str,
    thought_db_context: dict[str, Any],
    deps: WorkflowProgressQueryDeps,
) -> tuple[dict[str, Any], str, str]:
    """Run workflow_progress query and normalize return shape."""

    wf_prog_prompt = deps.workflow_progress_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
        workflow=active_workflow if isinstance(active_workflow, dict) else {},
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        latest_evidence=latest_evidence if isinstance(latest_evidence, dict) else {},
        last_batch_input=last_batch_input,
        hands_last_message=hands_last_message,
    )
    wf_prog_obj, wf_prog_ref, wf_prog_state = deps.mind_call(
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


def append_workflow_progress_event(
    *,
    batch_id: str,
    thread_id: str,
    active_workflow: dict[str, Any],
    wf_prog_obj: dict[str, Any],
    wf_prog_ref: str,
    wf_prog_state: str,
    evidence_append: Callable[[dict[str, Any]], Any],
    now_ts: Callable[[], str],
) -> dict[str, Any]:
    """Append workflow_progress event to EvidenceLog and return record."""

    rec = evidence_append(
        {
            "kind": "workflow_progress",
            "batch_id": f"{batch_id}.workflow_progress",
            "ts": now_ts(),
            "thread_id": thread_id,
            "workflow_id": str((active_workflow if isinstance(active_workflow, dict) else {}).get("id") or ""),
            "workflow_name": str((active_workflow if isinstance(active_workflow, dict) else {}).get("name") or ""),
            "state": str(wf_prog_state or ""),
            "mind_transcript_ref": str(wf_prog_ref or ""),
            "output": wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
        }
    )
    return rec if isinstance(rec, dict) else {}


def apply_workflow_progress_and_persist(
    *,
    batch_id: str,
    thread_id: str,
    active_workflow: dict[str, Any],
    workflow_run: dict[str, Any],
    wf_prog_obj: dict[str, Any],
    apply_workflow_progress_output_fn: Callable[..., bool],
    persist_overlay: Callable[[], None],
    now_ts: Callable[[], str],
) -> bool:
    """Apply workflow output and persist overlay when state changed."""

    changed = apply_workflow_progress_output_fn(
        active_workflow=active_workflow if isinstance(active_workflow, dict) else {},
        workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
        wf_progress_output=wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
        batch_id=batch_id,
        thread_id=str(thread_id or ""),
        now_ts=now_ts,
    )
    if not changed:
        return False
    persist_overlay()
    return True
