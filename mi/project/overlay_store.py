from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.paths import ProjectPaths, project_identity
from ..core.storage import read_json_best_effort, write_json_atomic


def _default_overlay(*, project_paths: ProjectPaths, project_root: Path, ident: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project_paths.project_id,
        "root_path": str(project_root.resolve()),
        "stack_hints": [],
        "testless_verification_strategy": {
            "chosen_once": False,
            # Derived cache pointer to the canonical Thought DB claim_id (project scope).
            # Keep this as a pointer (not full text) to avoid ambiguity about what is canonical.
            "claim_id": "",
            "rationale": "",
        },
        "host_bindings": [],
        "global_workflow_overrides": {},
        "hands_state": {
            "provider": "",
            "thread_id": "",
            "updated_ts": "",
        },
        "workflow_run": {
            "version": "v1",
            "active": False,
            "workflow_id": "",
            "workflow_name": "",
            "thread_id": "",
            "started_ts": "",
            "updated_ts": "",
            "completed_step_ids": [],
            "next_step_id": "",
            "last_batch_id": "",
            "last_confidence": 0.0,
            "last_notes": "",
            "close_reason": "",
        },
        "identity_key": str(ident.get("key") or "").strip(),
        "identity": ident if isinstance(ident, dict) else {},
    }


def _is_str_list(obj: Any) -> bool:
    return isinstance(obj, list) and all(isinstance(x, str) for x in obj)


def _is_testless_strategy(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return isinstance(obj.get("chosen_once"), bool) and isinstance(obj.get("claim_id"), str) and isinstance(obj.get("rationale"), str)


def _is_hands_state(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return isinstance(obj.get("provider"), str) and isinstance(obj.get("thread_id"), str) and isinstance(obj.get("updated_ts"), str)


def _is_workflow_run(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return (
        isinstance(obj.get("version"), str)
        and isinstance(obj.get("active"), bool)
        and isinstance(obj.get("workflow_id"), str)
        and isinstance(obj.get("workflow_name"), str)
        and isinstance(obj.get("thread_id"), str)
        and isinstance(obj.get("started_ts"), str)
        and isinstance(obj.get("updated_ts"), str)
        and _is_str_list(obj.get("completed_step_ids"))
        and isinstance(obj.get("next_step_id"), str)
        and isinstance(obj.get("last_batch_id"), str)
        and isinstance(obj.get("last_confidence"), (int, float))
        and isinstance(obj.get("last_notes"), str)
        and isinstance(obj.get("close_reason"), str)
    )


def _is_overlay_valid(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return (
        isinstance(obj.get("project_id"), str)
        and isinstance(obj.get("root_path"), str)
        and _is_str_list(obj.get("stack_hints"))
        and _is_testless_strategy(obj.get("testless_verification_strategy"))
        and isinstance(obj.get("host_bindings"), list)
        and isinstance(obj.get("global_workflow_overrides"), dict)
        and _is_hands_state(obj.get("hands_state"))
        and _is_workflow_run(obj.get("workflow_run"))
        and isinstance(obj.get("identity_key"), str)
        and isinstance(obj.get("identity"), dict)
    )


def load_project_overlay(*, home_dir: Path, project_root: Path, warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Load the per-project overlay.json store.

    Overlay is project-scoped state (hands thread id, workflow cursor, host bindings, etc.).
    Canonical values/preferences live in Thought DB, not here.
    """

    project_paths = ProjectPaths(home_dir=home_dir, project_root=project_root)
    ident = project_identity(project_root)
    defaults = _default_overlay(project_paths=project_paths, project_root=project_root, ident=ident)
    raw = read_json_best_effort(project_paths.overlay_path, default=None, label="overlay", warnings=warnings)
    changed = False
    if _is_overlay_valid(raw):
        overlay: dict[str, Any] = dict(raw)
    else:
        overlay = defaults
        changed = True

    if str(overlay.get("project_id") or "").strip() != project_paths.project_id:
        overlay["project_id"] = project_paths.project_id
        changed = True

    cur_root_path = str(project_root.resolve())
    if str(overlay.get("root_path") or "").strip() != cur_root_path:
        overlay["root_path"] = cur_root_path
        changed = True

    identity_key = str(ident.get("key") or "").strip()
    if identity_key and str(overlay.get("identity_key") or "").strip() != identity_key:
        overlay["identity_key"] = identity_key
        changed = True
    if isinstance(overlay.get("identity"), dict):
        if overlay.get("identity") != ident:
            overlay["identity"] = ident
            changed = True
    else:
        overlay["identity"] = ident
        changed = True

    if changed:
        write_json_atomic(project_paths.overlay_path, overlay)
    return overlay


def write_project_overlay(*, home_dir: Path, project_root: Path, overlay: dict[str, Any]) -> None:
    project_paths = ProjectPaths(home_dir=home_dir, project_root=project_root)
    write_json_atomic(project_paths.overlay_path, overlay if isinstance(overlay, dict) else {})
