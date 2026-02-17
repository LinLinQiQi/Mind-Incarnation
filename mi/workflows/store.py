from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.paths import GlobalPaths, ProjectPaths
from ..core.storage import ensure_dir, now_rfc3339, read_json, read_json_best_effort, write_json, write_json_atomic


WORKFLOW_IR_VERSION = "v1"


def new_workflow_id() -> str:
    return f"wf_{time.time_ns()}_{secrets.token_hex(4)}"


def _workflow_path_dir(workflows_dir: Path, workflow_id: str) -> Path:
    return Path(workflows_dir) / f"{workflow_id}.json"


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _normalize_workflow(obj: dict[str, Any]) -> dict[str, Any]:
    w = dict(obj)
    if "version" not in w:
        w["version"] = WORKFLOW_IR_VERSION
    if "id" not in w:
        w["id"] = ""
    if "name" not in w:
        w["name"] = ""
    if "enabled" not in w:
        w["enabled"] = True
    if "trigger" not in w or not isinstance(w.get("trigger"), dict):
        w["trigger"] = {"mode": "manual", "pattern": ""}
    else:
        t = dict(w["trigger"])
        if "mode" not in t:
            t["mode"] = "manual"
        if "pattern" not in t:
            t["pattern"] = ""
        w["trigger"] = t
    if "mermaid" not in w:
        w["mermaid"] = ""
    if "steps" not in w or not isinstance(w.get("steps"), list):
        w["steps"] = []
    if "source" not in w or not isinstance(w.get("source"), dict):
        w["source"] = {"kind": "manual", "reason": "", "evidence_refs": []}
    else:
        s = dict(w["source"])
        if "kind" not in s:
            s["kind"] = "manual"
        if "reason" not in s:
            s["reason"] = ""
        if "evidence_refs" not in s or not isinstance(s.get("evidence_refs"), list):
            s["evidence_refs"] = []
        w["source"] = s
    if "created_ts" not in w:
        w["created_ts"] = now_rfc3339()
    if "updated_ts" not in w:
        w["updated_ts"] = now_rfc3339()
    return w


def normalize_workflow(obj: dict[str, Any]) -> dict[str, Any]:
    """Public wrapper for workflow normalization (fills defaults; does not write)."""

    return _normalize_workflow(obj if isinstance(obj, dict) else {})


def apply_global_overrides(workflow: dict[str, Any], *, overlay: dict[str, Any]) -> dict[str, Any]:
    """Public wrapper: apply per-project overrides to a global workflow (non-destructive)."""

    return _apply_global_overrides(workflow, overlay=overlay if isinstance(overlay, dict) else {})


@dataclass(frozen=True)
class WorkflowStore:
    project_paths: ProjectPaths

    def list_ids(self) -> list[str]:
        d = self.project_paths.workflows_dir
        try:
            items = sorted(d.glob("wf_*.json"))
        except FileNotFoundError:
            return []
        ids: list[str] = []
        for p in items:
            if p.is_file() and p.suffix == ".json":
                ids.append(p.stem)
        return ids

    def load(self, workflow_id: str) -> dict[str, Any]:
        obj = read_json(_workflow_path_dir(self.project_paths.workflows_dir, workflow_id), default=None)
        if not isinstance(obj, dict):
            raise FileNotFoundError(f"workflow not found or invalid: {workflow_id}")
        return _normalize_workflow(obj)

    def write(self, workflow: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(workflow, dict):
            raise TypeError("workflow must be a dict")
        w = _normalize_workflow(workflow)
        wid = str(w.get("id") or "").strip()
        if not wid:
            raise ValueError("workflow.id is required")
        now = now_rfc3339()
        if not str(w.get("created_ts") or "").strip():
            w["created_ts"] = now
        w["updated_ts"] = now
        ensure_dir(self.project_paths.workflows_dir)
        write_json(_workflow_path_dir(self.project_paths.workflows_dir, wid), w)
        return w

    def delete(self, workflow_id: str) -> None:
        p = _workflow_path_dir(self.project_paths.workflows_dir, workflow_id)
        try:
            p.unlink()
        except FileNotFoundError:
            return

    def enabled_workflows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for wid in self.list_ids():
            try:
                w = self.load(wid)
            except Exception:
                continue
            if bool(w.get("enabled", False)):
                out.append(w)
        return out

    def fingerprint(self, *, enabled_only: bool = False) -> str:
        """Return a stable fingerprint of stored workflows (for auto-sync decisions)."""

        ids = self.list_ids()
        items: list[str] = []
        for wid in ids:
            try:
                w = self.load(wid)
            except Exception:
                continue
            if enabled_only and not bool(w.get("enabled", False)):
                continue
            items.append(_stable_json(w))
        digest = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()
        return digest[:16]


@dataclass(frozen=True)
class GlobalWorkflowStore:
    global_paths: GlobalPaths

    @property
    def workflows_dir(self) -> Path:
        return self.global_paths.global_workflows_dir

    def list_ids(self) -> list[str]:
        d = self.workflows_dir
        try:
            items = sorted(d.glob("wf_*.json"))
        except FileNotFoundError:
            return []
        ids: list[str] = []
        for p in items:
            if p.is_file() and p.suffix == ".json":
                ids.append(p.stem)
        return ids

    def load(self, workflow_id: str) -> dict[str, Any]:
        obj = read_json(_workflow_path_dir(self.workflows_dir, workflow_id), default=None)
        if not isinstance(obj, dict):
            raise FileNotFoundError(f"global workflow not found or invalid: {workflow_id}")
        return _normalize_workflow(obj)

    def write(self, workflow: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(workflow, dict):
            raise TypeError("workflow must be a dict")
        w = _normalize_workflow(workflow)
        wid = str(w.get("id") or "").strip()
        if not wid:
            raise ValueError("workflow.id is required")
        now = now_rfc3339()
        if not str(w.get("created_ts") or "").strip():
            w["created_ts"] = now
        w["updated_ts"] = now
        ensure_dir(self.workflows_dir)
        write_json(_workflow_path_dir(self.workflows_dir, wid), w)
        return w

    def delete(self, workflow_id: str) -> None:
        p = _workflow_path_dir(self.workflows_dir, workflow_id)
        try:
            p.unlink()
        except FileNotFoundError:
            return

    def enabled_workflows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for wid in self.list_ids():
            try:
                w = self.load(wid)
            except Exception:
                continue
            if bool(w.get("enabled", False)):
                out.append(w)
        return out

    def fingerprint(self, *, enabled_only: bool = False) -> str:
        ids = self.list_ids()
        items: list[str] = []
        for wid in ids:
            try:
                w = self.load(wid)
            except Exception:
                continue
            if enabled_only and not bool(w.get("enabled", False)):
                continue
            items.append(_stable_json(w))
        digest = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()
        return digest[:16]


def _apply_global_overrides(
    workflow: dict[str, Any],
    *,
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """Apply project overlay overrides to a global workflow (non-destructive)."""

    ov = overlay.get("global_workflow_overrides") if isinstance(overlay.get("global_workflow_overrides"), dict) else {}
    if not isinstance(ov, dict):
        return workflow
    wid = str(workflow.get("id") or "").strip()
    if not wid:
        return workflow
    entry = ov.get(wid) if isinstance(ov.get(wid), dict) else {}
    if not isinstance(entry, dict):
        return workflow

    w2 = dict(workflow)
    if "enabled" in entry:
        w2["enabled"] = bool(entry.get("enabled"))

    # Optional top-level patches (project-scoped) for global workflows.
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        w2["name"] = name.strip()
    mermaid = entry.get("mermaid")
    if isinstance(mermaid, str):
        w2["mermaid"] = mermaid
    trig = entry.get("trigger")
    if isinstance(trig, dict):
        # Replace trigger as a unit (normalize will fill defaults).
        w2["trigger"] = dict(trig)

    # Steps patching:
    # - If steps_replace is present, it replaces the entire steps list.
    # - Else, step_patches may patch or disable individual steps by id.
    steps_replace = entry.get("steps_replace")
    if isinstance(steps_replace, list):
        w2["steps"] = [x for x in steps_replace if isinstance(x, dict)]
        return _normalize_workflow(w2)

    step_patches = entry.get("step_patches") if isinstance(entry.get("step_patches"), dict) else {}
    if isinstance(step_patches, dict) and step_patches:
        steps = w2.get("steps") if isinstance(w2.get("steps"), list) else []
        out_steps: list[dict[str, Any]] = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "").strip()
            patch = step_patches.get(sid) if sid and isinstance(step_patches.get(sid), dict) else {}
            if not isinstance(patch, dict) or not patch:
                out_steps.append(s)
                continue
            if bool(patch.get("disabled", False)):
                continue
            s2 = dict(s)
            # Allow patching a conservative subset of fields (keep id stable).
            for k in ("kind", "title", "hands_input", "check_input", "risk_category", "policy", "notes"):
                if k in patch:
                    s2[k] = patch.get(k)
            out_steps.append(s2)
        w2["steps"] = out_steps

    return _normalize_workflow(w2)


@dataclass(frozen=True)
class WorkflowRegistry:
    """Merge project + global workflow stores (project always wins)."""

    project_store: WorkflowStore
    global_store: GlobalWorkflowStore

    def load_effective(self, workflow_id: str) -> dict[str, Any]:
        wid = str(workflow_id or "").strip()
        if not wid:
            raise FileNotFoundError("empty workflow id")
        try:
            w = self.project_store.load(wid)
            return dict(w, _mi_scope="project")
        except Exception:
            w = self.global_store.load(wid)
            return dict(w, _mi_scope="global")

    def enabled_workflows_effective(self, *, overlay: dict[str, Any]) -> list[dict[str, Any]]:
        """Return effective enabled workflows for this project (global + project; project overrides global)."""

        return self.workflows_effective(overlay=overlay, enabled_only=True)

    def workflows_effective(self, *, overlay: dict[str, Any], enabled_only: bool) -> list[dict[str, Any]]:
        """Return effective workflows for this project.

        - Applies project overlay overrides to global workflows.
        - Applies precedence: project workflow id shadows global workflow id.
        - When enabled_only=true, returns only enabled workflows after overrides.
        """

        out_by_id: dict[str, dict[str, Any]] = {}

        # Start with global (apply overlay overrides), then overlay project on top.
        for wid in self.global_store.list_ids():
            try:
                w = self.global_store.load(wid)
            except Exception:
                continue
            if not isinstance(w, dict):
                continue
            w2 = _apply_global_overrides(w, overlay=overlay if isinstance(overlay, dict) else {})
            wid2 = str(w2.get("id") or "").strip()
            if not wid2:
                continue
            out_by_id[wid2] = dict(w2, _mi_scope="global")

        for wid in self.project_store.list_ids():
            try:
                w = self.project_store.load(wid)
            except Exception:
                continue
            if not isinstance(w, dict):
                continue
            wid2 = str(w.get("id") or "").strip()
            if not wid2:
                continue
            out_by_id[wid2] = dict(w, _mi_scope="project")

        if enabled_only:
            out_by_id = {wid: w for wid, w in out_by_id.items() if bool(w.get("enabled", False))}

        # Stable list order: project first, then global, both sorted by id.
        proj_ids = sorted([wid for wid, w in out_by_id.items() if str(w.get("_mi_scope")) == "project"])
        glob_ids = sorted([wid for wid, w in out_by_id.items() if str(w.get("_mi_scope")) == "global"])
        return [out_by_id[wid] for wid in (proj_ids + glob_ids)]


def render_workflow_markdown(workflow: dict[str, Any]) -> str:
    w = _normalize_workflow(workflow if isinstance(workflow, dict) else {})
    wid = str(w.get("id") or "").strip()
    name = str(w.get("name") or "").strip()
    enabled = bool(w.get("enabled", False))
    trig = w.get("trigger") if isinstance(w.get("trigger"), dict) else {}
    trig_mode = str(trig.get("mode") or "").strip()
    trig_pat = str(trig.get("pattern") or "").strip()
    mermaid = str(w.get("mermaid") or "").strip()
    steps = w.get("steps") if isinstance(w.get("steps"), list) else []

    lines: list[str] = []
    title = name or wid or "workflow"
    lines.append(f"# {title}")
    if wid:
        lines.append(f"- id: `{wid}`")
    lines.append(f"- enabled: `{str(enabled).lower()}`")
    if trig_mode:
        lines.append(f"- trigger.mode: `{trig_mode}`")
    if trig_pat:
        lines.append(f"- trigger.pattern: `{trig_pat}`")
    lines.append("")

    if mermaid:
        lines.append("## Flow")
        lines.append("")
        lines.append("```mermaid")
        lines.append(mermaid)
        lines.append("```")
        lines.append("")

    lines.append("## Steps")
    lines.append("")
    if not steps:
        lines.append("(no steps)")
        lines.append("")
    else:
        for i, s in enumerate(steps, start=1):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or f"s{i}").strip()
            kind = str(s.get("kind") or "").strip()
            title2 = str(s.get("title") or "").strip()
            lines.append(f"### {i}. {title2 or sid}")
            if kind:
                lines.append(f"- kind: `{kind}`")
            hands_input = str(s.get("hands_input") or "").strip()
            check_input = str(s.get("check_input") or "").strip()
            notes = str(s.get("notes") or "").strip()
            risk_category = str(s.get("risk_category") or "").strip()
            policy = str(s.get("policy") or "").strip()
            if risk_category:
                lines.append(f"- risk_category: `{risk_category}`")
            if policy:
                lines.append(f"- policy: `{policy}`")
            if hands_input:
                lines.append("")
                lines.append("Hands input:")
                lines.append("")
                lines.append("```")
                lines.append(hands_input)
                lines.append("```")
            if check_input:
                lines.append("")
                lines.append("Check input:")
                lines.append("")
                lines.append("```")
                lines.append(check_input)
                lines.append("```")
            if notes:
                lines.append("")
                lines.append("Notes:")
                lines.append("")
                lines.append(notes)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def load_workflow_candidates(project_paths: ProjectPaths, *, warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    obj = read_json_best_effort(project_paths.workflow_candidates_path, default=None, label="workflow_candidates", warnings=warnings)
    if not isinstance(obj, dict):
        return {"version": "v1", "by_signature": {}}
    if "by_signature" not in obj or not isinstance(obj.get("by_signature"), dict):
        obj["by_signature"] = {}
    if "version" not in obj:
        obj["version"] = "v1"
    return obj


def write_workflow_candidates(project_paths: ProjectPaths, obj: dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        raise TypeError("candidates must be a dict")
    if "version" not in obj:
        obj["version"] = "v1"
    if "by_signature" not in obj or not isinstance(obj.get("by_signature"), dict):
        obj["by_signature"] = {}
    write_json_atomic(project_paths.workflow_candidates_path, obj)
