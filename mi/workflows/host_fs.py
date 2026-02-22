from __future__ import annotations

import os
from pathlib import Path

from ..core.storage import ensure_dir


def _is_rel_path(p: str) -> bool:
    s = str(p or "").strip()
    if not s:
        return False
    # Disallow absolute paths and parent traversal.
    return not s.startswith("/") and ".." not in Path(s).parts


def _safe_rel(p: str, *, default: str) -> str:
    s = str(p or "").strip()
    return s if _is_rel_path(s) else default


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

