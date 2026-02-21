from __future__ import annotations

from typing import Any


def match_workflow_for_task(*, task_text: str, workflows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the best enabled workflow for a task (longest trigger match wins)."""

    t = (task_text or "").lower()
    best: dict[str, Any] | None = None
    best_score = -1
    for w in workflows:
        if not isinstance(w, dict):
            continue
        trig = w.get("trigger") if isinstance(w.get("trigger"), dict) else {}
        mode = str(trig.get("mode") or "").strip()
        pat = str(trig.get("pattern") or "").strip()
        if mode != "task_contains" or not pat:
            continue
        if pat.lower() not in t:
            continue
        score = len(pat)
        if score > best_score:
            best = w
            best_score = score
    return best


def workflow_step_ids(workflow: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for s in workflow.get("steps") if isinstance(workflow.get("steps"), list) else []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if sid:
            ids.append(sid)
    return ids


def load_active_workflow(*, workflow_run: dict[str, Any], load_effective: Any) -> dict[str, Any] | None:
    """Load the currently active workflow from the effective registry (best-effort)."""

    if not bool(workflow_run.get("active", False)):
        return None
    wid = str(workflow_run.get("workflow_id") or "").strip()
    if not wid:
        return None
    try:
        return load_effective(wid)
    except Exception:
        return None
