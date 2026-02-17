from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .storage import ensure_dir, now_rfc3339, read_json, write_json


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


def resolve_cli_project_root(home_dir: Path, cd: str, *, cwd: Path | None = None) -> tuple[Path, str]:
    """Resolve an effective project root for CLI commands.

    Goals:
    - Reduce user burden: allow running MI commands from any subdir of a git repo
      without having to pass `--cd` to the repo root.
    - Preserve intentional "subproject roots": if the current working directory
      (or a provided `--cd`) was previously used as a distinct project root, keep it.

    Resolution order:
    1) Explicit `--cd` (if provided; supports `@last/@pinned/@alias`)
    2) $MI_PROJECT_ROOT (if set)
    3) If inside git and the current dir is not a known project root, use git toplevel
    4) If not inside git and a last-used project exists, use it
    5) Fall back to cwd

    Returns: (project_root_path, reason)
    """

    cd_s = str(cd or "").strip()
    if cd_s:
        if cd_s.startswith("@"):
            token = cd_s
            p = resolve_project_selection_token(home_dir, token)
            if p is None:
                return (cwd or Path.cwd()).resolve(), f"error:alias_missing:{token}"
            return p, f"arg:{token}"
        return Path(cd_s).expanduser().resolve(), "arg"

    env_root = str(os.environ.get("MI_PROJECT_ROOT") or "").strip()
    if env_root:
        p = Path(env_root).expanduser().resolve()
        if p.exists():
            return p, "env:MI_PROJECT_ROOT"

    cur = (cwd or Path.cwd()).resolve()
    ident_cur = project_identity(cur)
    key_cur = str(ident_cur.get("key") or "").strip()

    mapping = _load_project_index(home_dir)

    # If the current directory was previously used as a project root (e.g., a monorepo subproject),
    # keep it stable by default.
    if key_cur and mapping.get(key_cur):
        return cur, "known:cwd"

    git_top = str(ident_cur.get("git_toplevel") or "").strip()
    if git_top:
        top = Path(git_top).resolve()
        if top != cur:
            ident_top = project_identity(top)
            key_top = str(ident_top.get("key") or "").strip()
            if key_top and mapping.get(key_top):
                return top, "known:git_toplevel"
            return top, "git_toplevel"

    # Outside of git, allow falling back to the last-used project to reduce `--cd` burden.
    last = resolve_project_selection_token(home_dir, "@last")
    if last is not None:
        return last, "last"

    return cur, "cwd"


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
    def transcripts_dir(self) -> Path:
        return self.project_dir / "transcripts"

    @property
    def workflows_dir(self) -> Path:
        # Project workflow IR is stored in MI home as the source of truth.
        # (Global workflows live under GlobalPaths.global_workflows_dir.)
        return self.project_dir / "workflows"

    @property
    def thoughtdb_dir(self) -> Path:
        # Thought DB (Claims + Edges) is a durable store that references EvidenceLog event_id.
        return self.project_dir / "thoughtdb"

    @property
    def thoughtdb_claims_path(self) -> Path:
        return self.thoughtdb_dir / "claims.jsonl"

    @property
    def thoughtdb_edges_path(self) -> Path:
        return self.thoughtdb_dir / "edges.jsonl"

    @property
    def thoughtdb_nodes_path(self) -> Path:
        # Thought DB nodes (Decision/Action/Summary) are append-only and reference EvidenceLog event_id.
        return self.thoughtdb_dir / "nodes.jsonl"

    @property
    def workflow_candidates_path(self) -> Path:
        # Signature -> count mapping for "suggested workflow" mining.
        return self.project_dir / "workflow_candidates.json"

    @property
    def preference_candidates_path(self) -> Path:
        # Signature -> count mapping for mined preference suggestions.
        return self.project_dir / "preference_candidates.json"

    @property
    def segment_state_path(self) -> Path:
        # Persisted, best-effort segment buffer for checkpoint-based mining across runs.
        return self.project_dir / "segment_state.json"


@dataclass(frozen=True)
class GlobalPaths:
    home_dir: Path

    @property
    def global_dir(self) -> Path:
        # Global ledger + non-project artifacts that should still have event_id provenance.
        return self.home_dir / "global"

    @property
    def global_evidence_log_path(self) -> Path:
        # Append-only global EvidenceLog (values/prefs lifecycle, etc.).
        return self.global_dir / "evidence.jsonl"

    @property
    def project_selection_path(self) -> Path:
        # Non-canonical convenience state for "run from anywhere" project selection.
        return self.global_dir / "project_selection.json"

    @property
    def global_workflows_dir(self) -> Path:
        # Global workflow IR (source of truth, shared across projects; project can override).
        return self.home_dir / "workflows" / "global"

    @property
    def indexes_dir(self) -> Path:
        # Materialized views (e.g., text index) live here; ledger remains under projects/*.
        return self.home_dir / "indexes"

    @property
    def thoughtdb_dir(self) -> Path:
        # Thought DB global store (project stores live under projects/<id>/thoughtdb).
        return self.home_dir / "thoughtdb"

    @property
    def thoughtdb_global_dir(self) -> Path:
        return self.thoughtdb_dir / "global"

    @property
    def thoughtdb_global_claims_path(self) -> Path:
        return self.thoughtdb_global_dir / "claims.jsonl"

    @property
    def thoughtdb_global_edges_path(self) -> Path:
        return self.thoughtdb_global_dir / "edges.jsonl"

    @property
    def thoughtdb_global_nodes_path(self) -> Path:
        return self.thoughtdb_global_dir / "nodes.jsonl"


_PROJECT_SELECTION_VERSION = "v1"
_ALIAS_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def project_selection_path(home_dir: Path) -> Path:
    """Path to non-canonical project selection registry (last/pinned/aliases)."""

    return GlobalPaths(home_dir=Path(home_dir).expanduser().resolve()).project_selection_path


def _default_project_selection_obj() -> dict[str, object]:
    return {"version": _PROJECT_SELECTION_VERSION, "last": {}, "pinned": {}, "aliases": {}}


def load_project_selection(home_dir: Path) -> dict[str, object]:
    """Load the project selection registry (best-effort; never raises)."""

    path = project_selection_path(home_dir)
    try:
        obj = read_json(path, default=None)
    except Exception:
        obj = None
    if not isinstance(obj, dict):
        return _default_project_selection_obj()

    out = _default_project_selection_obj()
    out.update(obj)
    if not isinstance(out.get("aliases"), dict):
        out["aliases"] = {}
    if not isinstance(out.get("last"), dict):
        out["last"] = {}
    if not isinstance(out.get("pinned"), dict):
        out["pinned"] = {}
    if not isinstance(out.get("version"), str):
        out["version"] = _PROJECT_SELECTION_VERSION
    return out


def write_project_selection(home_dir: Path, obj: dict[str, object]) -> None:
    """Write the selection registry (best-effort)."""

    path = project_selection_path(home_dir)
    ensure_dir(path.parent)
    write_json(path, obj)


def _selection_entry_for_root(home_dir: Path, project_root: Path) -> dict[str, object]:
    root = Path(project_root).expanduser().resolve()
    pp = ProjectPaths(home_dir=Path(home_dir).expanduser().resolve(), project_root=root)
    ident = project_identity(root)
    return {
        "ts": now_rfc3339(),
        "root_path": str(root),
        "project_id": str(pp.project_id),
        "identity": ident,
    }


def record_last_project_selection(home_dir: Path, project_root: Path) -> dict[str, object]:
    """Set the `@last` project (best-effort). Returns the stored entry."""

    obj = load_project_selection(home_dir)
    entry = _selection_entry_for_root(home_dir, project_root)
    obj["last"] = entry
    write_project_selection(home_dir, obj)
    return entry


def set_pinned_project_selection(home_dir: Path, project_root: Path) -> dict[str, object]:
    """Set the `@pinned` project (best-effort). Returns the stored entry."""

    obj = load_project_selection(home_dir)
    entry = _selection_entry_for_root(home_dir, project_root)
    obj["pinned"] = entry
    write_project_selection(home_dir, obj)
    return entry


def clear_pinned_project_selection(home_dir: Path) -> None:
    obj = load_project_selection(home_dir)
    obj["pinned"] = {}
    write_project_selection(home_dir, obj)


def normalize_project_alias(name: str) -> str:
    n = str(name or "").strip()
    if not n:
        return ""
    if not _ALIAS_NAME_RX.fullmatch(n):
        return ""
    return n


def set_project_alias(home_dir: Path, *, name: str, project_root: Path) -> dict[str, object]:
    """Add/update an alias entry. Returns the stored entry."""

    alias = normalize_project_alias(name)
    if not alias:
        raise ValueError("invalid alias name (expected [A-Za-z0-9][A-Za-z0-9._-]{0,63})")

    obj = load_project_selection(home_dir)
    aliases = obj.get("aliases")
    if not isinstance(aliases, dict):
        aliases = {}
        obj["aliases"] = aliases
    entry = _selection_entry_for_root(home_dir, project_root)
    aliases[alias] = entry
    write_project_selection(home_dir, obj)
    return entry


def remove_project_alias(home_dir: Path, *, name: str) -> bool:
    alias = normalize_project_alias(name)
    if not alias:
        return False
    obj = load_project_selection(home_dir)
    aliases = obj.get("aliases")
    if not isinstance(aliases, dict):
        return False
    if alias not in aliases:
        return False
    del aliases[alias]
    write_project_selection(home_dir, obj)
    return True


def list_project_aliases(home_dir: Path) -> dict[str, dict[str, object]]:
    obj = load_project_selection(home_dir)
    aliases = obj.get("aliases")
    if not isinstance(aliases, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for k, v in aliases.items():
        ks = str(k or "").strip()
        if not ks:
            continue
        if isinstance(v, dict):
            out[ks] = v
    return out


def resolve_project_selection_token(home_dir: Path, token: str) -> Path | None:
    """Resolve `@last/@pinned/@alias` tokens into an existing root path."""

    tok = str(token or "").strip()
    if not tok:
        return None
    if tok.startswith("@"):
        tok = tok[1:]
    tok = tok.strip()
    if not tok:
        return None

    obj = load_project_selection(home_dir)

    entry: dict[str, object] | None = None
    if tok in ("last", "pinned"):
        x = obj.get(tok)
        entry = x if isinstance(x, dict) else None
    else:
        aliases = obj.get("aliases")
        if isinstance(aliases, dict):
            x = aliases.get(tok)
            entry = x if isinstance(x, dict) else None

    if not entry:
        return None
    root_path = entry.get("root_path")
    if not isinstance(root_path, str) or not root_path.strip():
        return None
    p = Path(root_path).expanduser().resolve()
    if not p.exists():
        return None
    return p
