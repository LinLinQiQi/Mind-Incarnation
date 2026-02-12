from __future__ import annotations

import json
import os
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

    # Write manifest.
    manifest = {
        "version": "v1",
        "host": binding.host,
        "project_id": project_id,
        "ts": now_rfc3339(),
        "files": sorted(new_files),
        "register_symlink_dirs": binding.register_symlink_dirs,
    }
    _write_manifest(binding, manifest)

    # Registration: symlink dirs into host-recognized locations (best-effort).
    reg_results: list[dict[str, Any]] = []
    for it in binding.register_symlink_dirs:
        src_rel = str(it.get("src") or "").strip()
        dst_rel = str(it.get("dst") or "").strip()
        if not _is_rel_path(src_rel) or not _is_rel_path(dst_rel):
            continue
        src = (gen_root / src_rel).resolve()
        dst = (binding.workspace_root / dst_rel).resolve()
        ok, note = _ensure_symlink(src=src, dst=dst)
        reg_results.append({"src": src_rel, "dst": dst_rel, "ok": ok, "note": note})

    return {
        "host": binding.host,
        "ok": True,
        "workspace_root": str(binding.workspace_root),
        "generated_root": str(gen_root),
        "workflows_n": len(index_items),
        "removed_files": removed,
        "register": reg_results,
    }


def sync_hosts_from_overlay(*, overlay: dict[str, Any], project_id: str, workflows: list[dict[str, Any]]) -> dict[str, Any]:
    bindings = parse_host_bindings(overlay if isinstance(overlay, dict) else {})
    results: list[dict[str, Any]] = []
    for b in bindings:
        results.append(sync_host_binding(binding=b, project_id=project_id, workflows=workflows))
    ok = all(bool(r.get("ok", False)) for r in results) if results else True
    return {"ok": ok, "results": results}

