from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import ensure_dir, now_rfc3339, read_json, write_json
from .workflows import render_workflow_markdown


def _is_rel_path(p: str) -> bool:
    s = str(p or "").strip()
    if not s:
        return False
    # Disallow absolute paths and parent traversal.
    return not s.startswith("/") and ".." not in Path(s).parts


def _safe_rel(p: str, *, default: str) -> str:
    s = str(p or "").strip()
    return s if _is_rel_path(s) else default


@dataclass(frozen=True)
class HostBinding:
    host: str
    workspace_root: Path
    enabled: bool
    generated_rel_dir: str
    register_symlink_dirs: list[dict[str, str]]  # [{"src": "...", "dst": "..."}]

    @property
    def generated_root(self) -> Path:
        return self.workspace_root / self.generated_rel_dir


def parse_host_bindings(overlay: dict[str, Any]) -> list[HostBinding]:
    bindings_raw = overlay.get("host_bindings") if isinstance(overlay.get("host_bindings"), list) else []
    out: list[HostBinding] = []
    for b in bindings_raw:
        if not isinstance(b, dict):
            continue
        host = str(b.get("host") or "").strip()
        ws = str(b.get("workspace_root") or "").strip()
        if not host or not ws:
            continue
        enabled = bool(b.get("enabled", True))
        generated_rel_dir = _safe_rel(str(b.get("generated_rel_dir") or ""), default=f".mi/generated/{host}")
        reg = b.get("register") if isinstance(b.get("register"), dict) else {}
        symlink_dirs = reg.get("symlink_dirs") if isinstance(reg.get("symlink_dirs"), list) else []
        norm_dirs: list[dict[str, str]] = []
        for it in symlink_dirs:
            if not isinstance(it, dict):
                continue
            src = str(it.get("src") or "").strip()
            dst = str(it.get("dst") or "").strip()
            if not _is_rel_path(src) or not _is_rel_path(dst):
                continue
            norm_dirs.append({"src": src, "dst": dst})
        out.append(
            HostBinding(
                host=host,
                workspace_root=Path(ws).expanduser().resolve(),
                enabled=enabled,
                generated_rel_dir=generated_rel_dir,
                register_symlink_dirs=norm_dirs,
            )
        )
    return out


def _manifest_path(binding: HostBinding) -> Path:
    return binding.generated_root / "manifest.json"


def _load_manifest(binding: HostBinding) -> dict[str, Any]:
    obj = read_json(_manifest_path(binding), default=None)
    return obj if isinstance(obj, dict) else {}


def _write_manifest(binding: HostBinding, obj: dict[str, Any]) -> None:
    write_json(_manifest_path(binding), obj)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except Exception:
        return


def _ensure_symlink(*, src: Path, dst: Path) -> tuple[bool, str]:
    """Ensure dst is a symlink pointing to src.

    Returns (ok, note).
    """

    ensure_dir(dst.parent)

    try:
        if dst.is_symlink():
            cur = os.readlink(dst)
            # os.readlink returns raw string; compare resolved paths best-effort.
            cur_p = (dst.parent / cur).resolve() if not os.path.isabs(cur) else Path(cur).resolve()
            if cur_p == src.resolve():
                return True, "ok"
            _safe_unlink(dst)
        elif dst.exists():
            return False, "exists_non_symlink"
    except Exception:
        # Fall back to trying to create a new symlink.
        pass

    try:
        os.symlink(str(src), str(dst))
        return True, "created"
    except Exception as e:
        return False, f"symlink_failed: {e}"


def _single_line(text: str) -> str:
    # Some host formats (e.g., OpenClaw skills frontmatter) expect single-line values.
    return " ".join((text or "").strip().split())


def _openclaw_skill_dirname(workflow_id: str) -> str:
    wid = (workflow_id or "").strip()
    # wf_123_abcd -> mi-wf-123-abcd
    wid = wid.replace("_", "-")
    wid = re.sub(r"[^A-Za-z0-9._-]+", "-", wid).strip("-")
    return f"mi-{wid}" if wid else "mi-workflow"


def _render_openclaw_skill_markdown(*, workflow: dict[str, Any], project_id: str) -> str:
    wid = _single_line(str(workflow.get("id") or ""))
    name = _single_line(str(workflow.get("name") or "")) or wid or "MI workflow"
    trig = workflow.get("trigger") if isinstance(workflow.get("trigger"), dict) else {}
    trig_mode = _single_line(str(trig.get("mode") or "manual"))
    trig_pat = _single_line(str(trig.get("pattern") or ""))
    mermaid = str(workflow.get("mermaid") or "").strip()
    steps = workflow.get("steps") if isinstance(workflow.get("steps"), list) else []

    meta_obj = {
        "mi": {
            "generated": True,
            "host": "openclaw",
            "project_id": project_id,
            "workflow_id": wid,
            "workflow_name": name,
        }
    }
    meta_json = json.dumps(meta_obj, sort_keys=True, separators=(",", ":"))

    lines: list[str] = []
    lines.append("---")
    # OpenClaw parses AgentSkills-compatible SKILL.md folders.
    lines.append(f"name: {_openclaw_skill_dirname(wid)}")
    lines.append(f"description: {_single_line(f'MI workflow: {name}')}")
    # OpenClaw expects metadata to be a single-line JSON string.
    lines.append(f"metadata: {meta_json}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {name}")
    lines.append("")
    lines.append("This skill is generated by Mind Incarnation (MI). Do not edit by hand.")
    lines.append("")
    lines.append("## When To Use")
    lines.append("")
    if trig_mode == "task_contains" and trig_pat:
        lines.append(f"- Use when the user task contains: `{trig_pat}`")
    else:
        lines.append("- Use when you want to follow this reusable workflow.")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Keep steps coarse-grained; do not force rigid step-by-step reporting.")
    lines.append("- If a step involves network/install/push/publish and it is not clearly safe, ask before doing it.")
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
            sid = _single_line(str(s.get("id") or f"s{i}"))
            kind = _single_line(str(s.get("kind") or ""))
            title = _single_line(str(s.get("title") or "")) or sid or f"step {i}"
            risk_category = _single_line(str(s.get("risk_category") or ""))
            policy = _single_line(str(s.get("policy") or ""))
            notes = str(s.get("notes") or "").strip()
            hands_input = str(s.get("hands_input") or "").strip()
            check_input = str(s.get("check_input") or "").strip()

            lines.append(f"### {i}. {title}")
            if kind:
                lines.append(f"- kind: `{kind}`")
            if risk_category:
                lines.append(f"- risk_category: `{risk_category}`")
            if policy:
                lines.append(f"- policy: `{policy}`")
            if notes:
                lines.append("")
                lines.append(notes)
            if hands_input:
                lines.append("")
                lines.append("Instruction:")
                lines.append("")
                lines.append("```")
                lines.append(hands_input)
                lines.append("```")
            if check_input:
                lines.append("")
                lines.append("Checks:")
                lines.append("")
                lines.append("```")
                lines.append(check_input)
                lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _register_openclaw_skills(
    *,
    binding: HostBinding,
    prev_manifest: dict[str, Any],
    gen_root: Path,
    skill_dirnames: list[str],
) -> dict[str, Any]:
    """Register generated OpenClaw skills into the host workspace.

    Strategy:
    - Generate skills under `<generated_root>/skills/<skill_dir>/SKILL.md`.
    - Symlink each skill dir into `<workspace_root>/skills/<skill_dir>`.

    This keeps registration granular and reversible.
    """

    host_skills_root = binding.workspace_root / "skills"
    ensure_dir(host_skills_root)

    prev_links: list[str] = []
    oc = prev_manifest.get("openclaw") if isinstance(prev_manifest.get("openclaw"), dict) else {}
    if isinstance(oc, dict):
        prev_links_raw = oc.get("registered_links") if isinstance(oc.get("registered_links"), list) else []
        for x in prev_links_raw:
            if isinstance(x, str) and x.strip():
                prev_links.append(x.strip())

    # Remove stale links created in the previous run.
    removed: list[str] = []
    keep = {f"skills/{d}" for d in skill_dirnames if d}
    for rel in prev_links:
        rel = rel.strip().lstrip("/")
        if not rel or rel in keep:
            continue
        # Never resolve() here: we want to remove the symlink itself, not its target.
        dst = binding.workspace_root / rel
        try:
            if dst.is_symlink():
                _safe_unlink(dst)
                removed.append(rel)
        except Exception:
            continue

    results: list[dict[str, Any]] = []
    registered: list[str] = []
    for d in skill_dirnames:
        if not d:
            continue
        src = (gen_root / "skills" / d).resolve()
        dst_rel = f"skills/{d}"
        dst = binding.workspace_root / dst_rel
        ok, note = _ensure_symlink(src=src, dst=dst)
        results.append({"dst": dst_rel, "src": f"skills/{d}", "ok": ok, "note": note})
        if ok:
            registered.append(dst_rel)

    return {
        "ok": all(r.get("ok", False) for r in results) if results else True,
        "registered_links": sorted(registered),
        "removed_links": removed,
        "results": results,
    }


def sync_host_binding(*, binding: HostBinding, project_id: str, workflows: list[dict[str, Any]]) -> dict[str, Any]:
    """Write derived workflow artifacts into the host workspace (generated dir + optional registration)."""

    if not binding.enabled:
        return {"host": binding.host, "ok": True, "skipped": True, "reason": "disabled"}

    if not binding.workspace_root.exists():
        return {"host": binding.host, "ok": False, "error": f"workspace_root not found: {binding.workspace_root}"}

    gen_root = binding.generated_root
    workflows_dir = gen_root / "workflows"
    ensure_dir(workflows_dir)

    # Remove stale files from the previous run (only within generated dir).
    prev = _load_manifest(binding)
    prev_files = prev.get("files") if isinstance(prev.get("files"), list) else []
    prev_paths: list[Path] = []
    for fp in prev_files:
        if isinstance(fp, str) and fp.strip():
            p = (gen_root / fp).resolve()
            # Only touch files inside our generated root.
            if gen_root.resolve() in p.parents:
                prev_paths.append(p)

    new_files: list[str] = []

    # Write per-workflow artifacts.
    index_items: list[dict[str, Any]] = []
    for w in workflows:
        if not isinstance(w, dict):
            continue
        wid = str(w.get("id") or "").strip()
        if not wid:
            continue
        md_rel = f"workflows/{wid}.md"
        json_rel = f"workflows/{wid}.json"

        (gen_root / md_rel).write_text(render_workflow_markdown(w), encoding="utf-8")
        (gen_root / json_rel).write_text(json.dumps(w, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        new_files.extend([md_rel, json_rel])
        index_items.append({"id": wid, "name": str(w.get("name") or ""), "enabled": bool(w.get("enabled", False)), "md": md_rel, "json": json_rel})

    # Index.
    index_rel = "workflows/index.json"
    (gen_root / index_rel).write_text(json.dumps({"version": "v1", "items": index_items}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    new_files.append(index_rel)

    readme_rel = "README.md"
    readme_lines = [
        "# MI Generated Artifacts",
        "",
        f"- host: `{binding.host}`",
        f"- project_id: `{project_id}`",
        f"- generated_ts: `{now_rfc3339()}`",
        "",
        "## Workflows",
        "",
    ]
    if not index_items:
        readme_lines.append("(none)")
    else:
        for it in index_items:
            readme_lines.append(f"- `{it.get('id')}` {it.get('name')}")
    (gen_root / readme_rel).write_text("\n".join(readme_lines).rstrip() + "\n", encoding="utf-8")
    new_files.append(readme_rel)

    # Host-specific derived artifacts.
    openclaw_skill_dirs: list[str] = []
    if binding.host.strip().lower() == "openclaw":
        skills_root = gen_root / "skills"
        ensure_dir(skills_root)

        skill_items: list[dict[str, Any]] = []
        for w in workflows:
            if not isinstance(w, dict):
                continue
            wid = str(w.get("id") or "").strip()
            if not wid:
                continue
            d = _openclaw_skill_dirname(wid)
            openclaw_skill_dirs.append(d)

            skill_md_rel = f"skills/{d}/SKILL.md"
            wf_rel = f"skills/{d}/workflow.json"
            ensure_dir((gen_root / skill_md_rel).parent)
            (gen_root / skill_md_rel).write_text(_render_openclaw_skill_markdown(workflow=w, project_id=project_id), encoding="utf-8")
            (gen_root / wf_rel).write_text(json.dumps(w, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            new_files.extend([skill_md_rel, wf_rel])
            skill_items.append({"dir": d, "workflow_id": wid, "name": str(w.get("name") or ""), "skill_md": skill_md_rel})

        skills_index_rel = "skills/index.json"
        (gen_root / skills_index_rel).write_text(json.dumps({"version": "v1", "project_id": project_id, "items": skill_items}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        new_files.append(skills_index_rel)

    # Cleanup stale files.
    new_set = {str(x) for x in new_files}
    removed: list[str] = []
    for p in prev_paths:
        rel = None
        try:
            rel = str(p.relative_to(gen_root))
        except Exception:
            rel = None
        if rel and rel not in new_set:
            try:
                p.unlink()
                removed.append(rel)
            except Exception:
                pass

    # Registration: symlink dirs into host-recognized locations (best-effort).
    reg_results: list[dict[str, Any]] = []
    for it in binding.register_symlink_dirs:
        src_rel = str(it.get("src") or "").strip()
        dst_rel = str(it.get("dst") or "").strip()
        if not _is_rel_path(src_rel) or not _is_rel_path(dst_rel):
            continue
        src = (gen_root / src_rel).resolve()
        # Do not resolve(): we want to manage the symlink path itself, not its target.
        dst = binding.workspace_root / dst_rel
        ok, note = _ensure_symlink(src=src, dst=dst)
        reg_results.append({"src": src_rel, "dst": dst_rel, "ok": ok, "note": note})

    openclaw_reg: dict[str, Any] = {}
    if binding.host.strip().lower() == "openclaw":
        openclaw_reg = _register_openclaw_skills(
            binding=binding,
            prev_manifest=prev,
            gen_root=gen_root,
            skill_dirnames=sorted(set(openclaw_skill_dirs)),
        )

    # Write manifest (includes any host registration results needed for cleanup on next sync).
    manifest: dict[str, Any] = {
        "version": "v1",
        "host": binding.host,
        "project_id": project_id,
        "ts": now_rfc3339(),
        "files": sorted(new_files),
        "register_symlink_dirs": binding.register_symlink_dirs,
    }
    if binding.host.strip().lower() == "openclaw":
        manifest["openclaw"] = {
            "registered_links": openclaw_reg.get("registered_links", []) if isinstance(openclaw_reg, dict) else [],
        }
    _write_manifest(binding, manifest)

    ok_register = all(bool(r.get("ok", False)) for r in reg_results) if reg_results else True
    ok_openclaw = bool(openclaw_reg.get("ok", True)) if isinstance(openclaw_reg, dict) else True

    return {
        "host": binding.host,
        "ok": bool(ok_register and ok_openclaw),
        "workspace_root": str(binding.workspace_root),
        "generated_root": str(gen_root),
        "workflows_n": len(index_items),
        "removed_files": removed,
        "register": reg_results,
        "openclaw": openclaw_reg,
    }


def sync_hosts_from_overlay(*, overlay: dict[str, Any], project_id: str, workflows: list[dict[str, Any]]) -> dict[str, Any]:
    bindings = parse_host_bindings(overlay if isinstance(overlay, dict) else {})
    results: list[dict[str, Any]] = []
    for b in bindings:
        results.append(sync_host_binding(binding=b, project_id=project_id, workflows=workflows))
    ok = all(bool(r.get("ok", False)) for r in results) if results else True
    return {"ok": ok, "results": results}
