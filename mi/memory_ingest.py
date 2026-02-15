from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .memory_backends.base import MemoryBackend
from .memory_text import truncate
from .memory_types import MemoryGroup, MemoryItem
from .paths import GlobalPaths, ProjectPaths
from .storage import iter_jsonl, now_rfc3339, read_json
from .workflows import render_workflow_markdown


def iter_project_ids(home_dir: Path) -> Iterable[str]:
    projects = Path(home_dir).expanduser().resolve() / "projects"
    if not projects.is_dir():
        return []
    out: list[str] = []
    for d in sorted(projects.iterdir()):
        if d.is_dir() and d.name and not d.name.startswith("."):
            out.append(d.name)
    return out


def _learned_items_for_file(*, learned_path: Path, scope: str, project_id: str) -> list[MemoryItem]:
    disables: set[str] = set()
    for entry in iter_jsonl(learned_path):
        if not isinstance(entry, dict):
            continue
        if entry.get("action") == "disable" and entry.get("target_id"):
            disables.add(str(entry["target_id"]))

    out: list[MemoryItem] = []
    for entry in iter_jsonl(learned_path):
        if not isinstance(entry, dict):
            continue
        if entry.get("action") == "disable":
            continue
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id or entry_id in disables:
            continue
        if not bool(entry.get("enabled", True)):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        ts = str(entry.get("ts") or "").strip() or now_rfc3339()
        rationale = str(entry.get("rationale") or "").strip()
        title = text
        body = text + ("\n\nrationale: " + rationale if rationale else "")
        out.append(
            MemoryItem(
                item_id=f"learned:{scope}:{project_id or 'global'}:{entry_id}",
                kind="learned",
                scope=scope,
                project_id=project_id,
                ts=ts,
                title=truncate(title, 120),
                body=truncate(body, 2400),
                tags=["learned", scope],
                source_refs=[{"kind": "learned_file", "path": str(learned_path), "entry_id": entry_id}],
            )
        )
    return out


def _workflow_items_for_dir(*, workflows_dir: Path, scope: str, project_id: str) -> list[MemoryItem]:
    out: list[MemoryItem] = []
    try:
        paths = sorted(workflows_dir.glob("wf_*.json"))
    except Exception:
        return []
    for p in paths:
        obj = read_json(p, default=None)
        if not isinstance(obj, dict):
            continue
        if not bool(obj.get("enabled", False)):
            continue
        wid = str(obj.get("id") or "").strip() or p.stem
        name = str(obj.get("name") or "").strip() or wid
        ts = str(obj.get("updated_ts") or obj.get("created_ts") or "").strip() or now_rfc3339()
        trig = obj.get("trigger") if isinstance(obj.get("trigger"), dict) else {}
        mode = str(trig.get("mode") or "").strip()
        pat = str(trig.get("pattern") or "").strip()

        # Index the rendered markdown: it is compact and includes steps.
        md = render_workflow_markdown(obj)
        body = "\n".join(
            [
                f"name: {name}",
                f"id: {wid}",
                (f"trigger: {mode} {pat}".strip() if mode or pat else "trigger: (none)"),
                "",
                md.strip(),
            ]
        ).strip()

        tags = ["workflow", scope]
        if mode:
            tags.append("trigger:" + mode)
        out.append(
            MemoryItem(
                item_id=f"workflow:{scope}:{project_id or 'global'}:{wid}",
                kind="workflow",
                scope=scope,
                project_id=project_id,
                ts=ts,
                title=truncate(name, 140),
                body=truncate(body, 6000),
                tags=tags,
                source_refs=[{"kind": "workflow_file", "path": str(p), "workflow_id": wid}],
            )
        )
    return out


def ingest_learned_and_workflows(*, home_dir: Path, backend: MemoryBackend) -> None:
    """Best-effort ingestion for small structured stores (no event log scanning)."""

    gp = GlobalPaths(home_dir=Path(home_dir).expanduser().resolve())
    groups: list[MemoryGroup] = []

    # Global learned.
    gl = _learned_items_for_file(learned_path=gp.learned_path, scope="global", project_id="")
    groups.append(MemoryGroup(kind="learned", scope="global", project_id="", items=gl))

    # Global workflows.
    wf_global_dir = gp.global_workflows_dir
    gw = _workflow_items_for_dir(workflows_dir=wf_global_dir, scope="global", project_id="")
    groups.append(MemoryGroup(kind="workflow", scope="global", project_id="", items=gw))

    # Per-project learned + workflows.
    project_ids = {str(pid).strip() for pid in iter_project_ids(gp.home_dir) if str(pid).strip()}
    for pid in sorted(project_ids):
        pp = ProjectPaths(home_dir=gp.home_dir, project_root=Path("."), _project_id=pid)  # project_root unused when _project_id provided
        pl = _learned_items_for_file(learned_path=pp.learned_path, scope="project", project_id=pid)
        pw = _workflow_items_for_dir(workflows_dir=pp.workflows_dir, scope="project", project_id=pid)
        groups.append(MemoryGroup(kind="learned", scope="project", project_id=pid, items=pl))
        groups.append(MemoryGroup(kind="workflow", scope="project", project_id=pid, items=pw))

    backend.sync_groups(groups, existing_project_ids=project_ids)
