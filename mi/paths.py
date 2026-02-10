from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .storage import ensure_dir, read_json, write_json


def default_home_dir() -> Path:
    return Path(os.environ.get("MI_HOME") or Path.home() / ".mind-incarnation")


def project_id_for_root(root: Path) -> str:
    """Legacy project id: stable only as long as the absolute root path is stable."""

    root_str = str(root.resolve())
    digest = hashlib.sha256(root_str.encode("utf-8")).hexdigest()
    return digest[:12]


def _projects_dir(home_dir: Path) -> Path:
    return home_dir / "projects"


def project_index_path(home_dir: Path) -> Path:
    return _projects_dir(home_dir) / "index.json"


def _load_project_index(home_dir: Path) -> dict[str, str]:
    obj = read_json(project_index_path(home_dir), default=None)
    if not isinstance(obj, dict):
        return {}
    # Preferred shape: {"version": "...", "by_identity": {...}}
    by_id = obj.get("by_identity")
    if isinstance(by_id, dict):
        out: dict[str, str] = {}
        for k, v in by_id.items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs:
                out[ks] = vs
        return out
    # Back-compat: allow a plain mapping object.
    out2: dict[str, str] = {}
    for k, v in obj.items():
        ks = str(k).strip()
        vs = str(v).strip()
        if ks and vs:
            out2[ks] = vs
    return out2


def _write_project_index(home_dir: Path, mapping: dict[str, str]) -> None:
    path = project_index_path(home_dir)
    ensure_dir(path.parent)
    write_json(path, {"version": "v1", "by_identity": dict(mapping)})


def _normalize_git_remote(url: str) -> str:
    """Normalize a git remote URL into a reasonably stable key string.

    This is best-effort; we primarily want the same repo cloned via different URL
    forms to map to the same identity key.
    """

    u = (url or "").strip()
    if not u:
        return ""

    if u.endswith(".git"):
        u = u[:-4]

    # scp-like: git@github.com:Owner/Repo -> github.com/Owner/Repo
    m = re.match(r"^[^@]+@([^:]+):(.+)$", u)
    if m:
        host = m.group(1).strip().lower()
        path = m.group(2).strip().lstrip("/")
        return host + "/" + path

    # URL-like: https://github.com/Owner/Repo -> github.com/Owner/Repo
    if "://" in u:
        try:
            p = urlparse(u)
            host = (p.netloc or "").strip().lower()
            path = (p.path or "").strip().lstrip("/")
            if host and path:
                return host + "/" + path
        except Exception:
            pass

    return u


def _run_git(root: Path, args: list[str], *, timeout_s: float, limit: int) -> str:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception:
        return ""
    out = (p.stdout or "").strip()
    if p.returncode != 0 and not out:
        out = (p.stderr or "").strip()
    out = out.strip()
    if len(out) > limit:
        out = out[: limit - 3] + "..."
    return out


def project_identity(project_root: Path) -> dict[str, str]:
    """Compute a best-effort identity dict for a project root.

    - For git repos: uses remote origin URL when available, plus a stable relpath
      within the repo (so different subprojects within a monorepo don't collide).
    - For non-git: falls back to the resolved absolute path.
    """

    root = project_root.resolve()

    if shutil.which("git"):
        inside = _run_git(root, ["rev-parse", "--is-inside-work-tree"], timeout_s=1, limit=50).lower() == "true"
    else:
        inside = False

    if not inside:
        root_s = str(root)
        return {"kind": "path", "key": "path:" + root_s, "root_path": root_s}

    toplevel = _run_git(root, ["rev-parse", "--show-toplevel"], timeout_s=1, limit=2000).strip()
    toplevel_p = Path(toplevel).resolve() if toplevel else root

    origin = _run_git(toplevel_p, ["config", "--get", "remote.origin.url"], timeout_s=1, limit=4000).strip()
    origin_norm = _normalize_git_remote(origin)

    root_commit = _run_git(toplevel_p, ["rev-list", "--max-parents=0", "HEAD"], timeout_s=2, limit=2000).splitlines()
    root_commit_s = root_commit[0].strip() if root_commit else ""
    if root_commit_s and not re.fullmatch(r"[0-9a-fA-F]{7,40}", root_commit_s):
        # No commits yet (or unexpected output); don't treat stderr as a commit id.
        root_commit_s = ""

    rel = os.path.relpath(str(root), str(toplevel_p))
    rel = rel.replace("\\", "/")
    if rel == ".":
        rel = ""

    repo_key = ""
    if origin_norm:
        repo_key = "origin:" + origin_norm
    elif root_commit_s:
        repo_key = "root:" + root_commit_s
    else:
        repo_key = "toplevel:" + str(toplevel_p)

    key = "git:" + repo_key + (":" + rel if rel else "")
    return {
        "kind": "git",
        "key": key,
        "repo_key": repo_key,
        "git_toplevel": str(toplevel_p),
        "git_relpath": rel,
        "git_origin": origin,
        "git_origin_norm": origin_norm,
        "git_root_commit": root_commit_s,
        "root_path": str(root),
    }


def _scan_for_existing_project_id(home_dir: Path, *, identity_key: str, root_path: str) -> str:
    projects = _projects_dir(home_dir)
    if not projects.is_dir():
        return ""

    # Pass 1: prefer identity_key matches (survives moves).
    if identity_key:
        for d in projects.iterdir():
            if not d.is_dir():
                continue
            overlay_path = d / "overlay.json"
            if not overlay_path.is_file():
                continue
            overlay = read_json(overlay_path, default=None)
            if not isinstance(overlay, dict):
                continue
            if str(overlay.get("identity_key") or "").strip() == identity_key:
                return d.name
            ident = overlay.get("identity")
            if isinstance(ident, dict) and str(ident.get("key") or "").strip() == identity_key:
                return d.name

    # Pass 2: root_path match (legacy behavior for stable paths).
    if root_path:
        for d in projects.iterdir():
            if not d.is_dir():
                continue
            overlay_path = d / "overlay.json"
            if not overlay_path.is_file():
                continue
            overlay = read_json(overlay_path, default=None)
            if not isinstance(overlay, dict):
                continue
            if str(overlay.get("root_path") or "").strip() == root_path:
                return d.name

    return ""


def resolve_project_id(home_dir: Path, project_root: Path) -> str:
    """Resolve the project id for a root path, with move/clone stability.

    Strategy:
    - Prefer index mapping by computed identity_key.
    - Otherwise, reuse the legacy id directory if it exists for the current root.
    - Otherwise, scan existing overlay.json files for a matching identity_key/root_path.
    - Finally, fall back to the legacy id.
    """

    root = project_root.resolve()
    legacy_id = project_id_for_root(root)
    projects_dir = _projects_dir(home_dir)
    legacy_dir = projects_dir / legacy_id

    ident = project_identity(root)
    identity_key = str(ident.get("key") or "").strip()

    mapping = _load_project_index(home_dir)
    if identity_key:
        mapped = str(mapping.get(identity_key) or "").strip()
        if mapped:
            if (projects_dir / mapped).is_dir():
                return mapped
            # Drop stale mappings.
            if identity_key in mapping:
                del mapping[identity_key]
                _write_project_index(home_dir, mapping)

    # If the old directory exists for this exact root path, keep using it.
    if legacy_dir.is_dir():
        pid = legacy_id
    else:
        pid = _scan_for_existing_project_id(home_dir, identity_key=identity_key, root_path=str(root))
        if not pid:
            pid = legacy_id

    # Persist mapping for future path moves (best-effort).
    if identity_key and mapping.get(identity_key) != pid:
        mapping[identity_key] = pid
        _write_project_index(home_dir, mapping)

    return pid


@dataclass(frozen=True)
class ProjectPaths:
    home_dir: Path
    project_root: Path
    _project_id: str = ""

    def __post_init__(self) -> None:
        if not self._project_id:
            object.__setattr__(self, "_project_id", resolve_project_id(self.home_dir, self.project_root))

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def project_dir(self) -> Path:
        return _projects_dir(self.home_dir) / self.project_id

    @property
    def overlay_path(self) -> Path:
        return self.project_dir / "overlay.json"

    @property
    def evidence_log_path(self) -> Path:
        return self.project_dir / "evidence.jsonl"

    @property
    def learned_path(self) -> Path:
        return self.project_dir / "learned.jsonl"

    @property
    def transcripts_dir(self) -> Path:
        return self.project_dir / "transcripts"


@dataclass(frozen=True)
class GlobalPaths:
    home_dir: Path

    @property
    def minds_dir(self) -> Path:
        return self.home_dir / "mindspec"

    @property
    def base_path(self) -> Path:
        return self.minds_dir / "base.json"

    @property
    def learned_path(self) -> Path:
        return self.minds_dir / "learned.jsonl"
