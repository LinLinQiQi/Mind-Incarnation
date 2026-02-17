from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.paths import ProjectPaths, project_identity
from ..core.storage import read_json, write_json


def load_project_overlay(*, home_dir: Path, project_root: Path) -> dict[str, Any]:
    """Load (and forward-fill) the per-project overlay.json store.

    Overlay is project-scoped state (hands thread id, workflow cursor, host bindings, etc.).
    Canonical values/preferences live in Thought DB, not here.
    """

    project_paths = ProjectPaths(home_dir=home_dir, project_root=project_root)
    overlay = read_json(project_paths.overlay_path, default=None)
    changed = False
    if overlay is None:
        overlay = {}
        changed = True

    if not isinstance(overlay, dict):
        overlay = {}
        changed = True

    def ensure_key(k: str, v: Any) -> None:
        nonlocal changed
        if k not in overlay:
            overlay[k] = v
            changed = True

    ensure_key("project_id", project_paths.project_id)
    ensure_key("root_path", str(project_root.resolve()))
    ensure_key("stack_hints", [])
    ensure_key(
        "testless_verification_strategy",
        {
            "chosen_once": False,
            "strategy": "",
            "rationale": "",
        },
    )
    ensure_key("host_bindings", [])
    ensure_key("global_workflow_overrides", {})
    ensure_key(
        "hands_state",
        {
            "provider": "",
            "thread_id": "",
            "updated_ts": "",
        },
    )
    ensure_key(
        "workflow_run",
        {
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
    )

    # Update derived identity fields (used for stable cross-path resolution).
    ident = project_identity(project_root)
    identity_key = str(ident.get("key") or "").strip()
    ensure_key("identity_key", identity_key)
    ensure_key("identity", ident if isinstance(ident, dict) else {})

    if str(overlay.get("project_id") or "").strip() != project_paths.project_id:
        overlay["project_id"] = project_paths.project_id
        changed = True

    cur_root_path = str(project_root.resolve())
    if str(overlay.get("root_path") or "").strip() != cur_root_path:
        overlay["root_path"] = cur_root_path
        changed = True

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

    # Patch nested keys for forward-compat.
    hs = overlay.get("hands_state")
    if not isinstance(hs, dict):
        overlay["hands_state"] = {"provider": "", "thread_id": "", "updated_ts": ""}
        changed = True
    else:
        for k, default_v in (("provider", ""), ("thread_id", ""), ("updated_ts", "")):
            if k not in hs:
                hs[k] = default_v
                changed = True

    gwo = overlay.get("global_workflow_overrides")
    if not isinstance(gwo, dict):
        overlay["global_workflow_overrides"] = {}
        changed = True

    wr = overlay.get("workflow_run")
    if not isinstance(wr, dict):
        overlay["workflow_run"] = {
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
        }
        changed = True
    else:
        for k, default_v in (
            ("version", "v1"),
            ("active", False),
            ("workflow_id", ""),
            ("workflow_name", ""),
            ("thread_id", ""),
            ("started_ts", ""),
            ("updated_ts", ""),
            ("completed_step_ids", []),
            ("next_step_id", ""),
            ("last_batch_id", ""),
            ("last_confidence", 0.0),
            ("last_notes", ""),
            ("close_reason", ""),
        ):
            if k not in wr:
                wr[k] = default_v
                changed = True

    if changed:
        write_json(project_paths.overlay_path, overlay)
    return overlay


def write_project_overlay(*, home_dir: Path, project_root: Path, overlay: dict[str, Any]) -> None:
    project_paths = ProjectPaths(home_dir=home_dir, project_root=project_root)
    write_json(project_paths.overlay_path, overlay if isinstance(overlay, dict) else {})

