from __future__ import annotations

from typing import Any, Callable

from .workflow_cursor import workflow_step_ids


def apply_workflow_progress_output(
    *,
    active_workflow: dict[str, Any],
    workflow_run: dict[str, Any],
    wf_progress_output: dict[str, Any],
    batch_id: str,
    thread_id: str,
    now_ts: Callable[[], str],
) -> bool:
    """Apply workflow_progress output onto workflow_run (best-effort)."""

    if not isinstance(active_workflow, dict) or not active_workflow:
        return False
    if not isinstance(workflow_run, dict):
        return False
    if not isinstance(wf_progress_output, dict) or not bool(wf_progress_output.get("should_update", False)):
        return False

    step_allow = set(workflow_step_ids(active_workflow))
    raw_done = wf_progress_output.get("completed_step_ids") if isinstance(wf_progress_output.get("completed_step_ids"), list) else []
    done_ids: list[str] = []
    seen_done: set[str] = set()
    for x in raw_done:
        xs = str(x or "").strip()
        if not xs or xs in seen_done:
            continue
        if xs not in step_allow:
            continue
        seen_done.add(xs)
        done_ids.append(xs)

    nxt = str(wf_progress_output.get("next_step_id") or "").strip()
    if nxt and nxt not in step_allow:
        nxt = ""
    if not nxt:
        # Deterministic fallback: first step not marked done (list order).
        for sid in workflow_step_ids(active_workflow):
            if sid not in seen_done:
                nxt = sid
                break

    workflow_run["version"] = str(workflow_run.get("version") or "v1")
    workflow_run["active"] = bool(workflow_run.get("active", True))
    workflow_run["workflow_id"] = str(active_workflow.get("id") or workflow_run.get("workflow_id") or "")
    workflow_run["workflow_name"] = str(active_workflow.get("name") or workflow_run.get("workflow_name") or "")
    if thread_id and thread_id != "unknown":
        workflow_run["thread_id"] = thread_id
    workflow_run["completed_step_ids"] = done_ids
    workflow_run["next_step_id"] = nxt
    workflow_run["last_batch_id"] = str(batch_id or "")
    workflow_run["last_confidence"] = wf_progress_output.get("confidence")
    workflow_run["last_notes"] = str(wf_progress_output.get("notes") or "").strip()
    workflow_run["updated_ts"] = now_ts()

    should_close = bool(wf_progress_output.get("should_close", False))
    if should_close or not nxt:
        workflow_run["active"] = False
        workflow_run["close_reason"] = str(wf_progress_output.get("close_reason") or "").strip()

    return True
