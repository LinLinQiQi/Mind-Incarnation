from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        data = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    return json.loads(data)


def write_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


def iter_jsonl(path: Path) -> Iterable[Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
    except FileNotFoundError:
        return


def now_rfc3339() -> str:
    # time.strftime doesn't include sub-second; that's fine for V1.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON to `path` atomically (best-effort, no fsync)."""

    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


def write_json_atomic(path: Path, obj: Any) -> None:
    """Write JSON via atomic replace (preferred for MI-owned state)."""

    atomic_write_json(path, obj)


def _env_tristate_bool(name: str) -> bool | None:
    """Parse an environment variable into a tri-state boolean.

    - unset/empty -> None
    - truthy -> True
    - falsy -> False
    - unknown non-empty -> True (prefer being loud over silently hiding warnings)
    """

    raw = os.environ.get(name)
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return True


def filename_safe_ts(ts: str) -> str:
    """Convert an RFC3339 timestamp into a filename-safe stamp.

    Example: 2026-02-22T12:34:56Z -> 20260222T123456Z
    """

    return str(ts or "").replace("-", "").replace(":", "")


def _quarantine_corrupt_file(path: Path) -> tuple[str, str]:
    """Best-effort quarantine: rename `path` to `path.corrupt.<ts>[.<n>]`.

    Returns (quarantined_to, error). If quarantine fails, quarantined_to is "".
    """

    p = Path(path).expanduser().resolve()
    stamp = filename_safe_ts(now_rfc3339())
    base = Path(str(p) + f".corrupt.{stamp}")
    dest = base
    for i in range(1, 100):
        if not dest.exists():
            break
        dest = Path(str(base) + f".{i}")
    try:
        p.rename(dest)
        return str(dest), ""
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def read_json_best_effort(
    path: Path,
    default: Any = None,
    *,
    label: str = "",
    warnings: list[dict[str, Any]] | None = None,
) -> Any:
    """Read JSON but tolerate corruption by quarantining and returning default.

    This is intended for MI-owned *state* files (overlay/segment_state/candidates/manifest).
    It is NOT used for user-authored config by default.
    """

    p = Path(path).expanduser().resolve()
    try:
        data = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except Exception as e:
        quarantined_to, qerr = _quarantine_corrupt_file(p)
        item = {
            "path": str(p),
            "label": (str(label or "").strip() or p.name),
            "error": f"{type(e).__name__}: {e}",
            "quarantined_to": quarantined_to,
            "quarantine_error": qerr,
            "used_default": True,
        }
        if warnings is not None:
            warnings.append(item)
        force = _env_tristate_bool("MI_STATE_WARNINGS_STDERR")
        should_print = force if force is not None else (warnings is None)
        if should_print:
            print(f"[mi] state read failed; quarantined and continued. label={item['label']} path={item['path']}", file=sys.stderr)
        return default

    try:
        return json.loads(data)
    except Exception as e:
        quarantined_to, qerr = _quarantine_corrupt_file(p)
        item = {
            "path": str(p),
            "label": (str(label or "").strip() or p.name),
            "error": f"{type(e).__name__}: {e}",
            "quarantined_to": quarantined_to,
            "quarantine_error": qerr,
            "used_default": True,
        }
        if warnings is not None:
            warnings.append(item)
        force = _env_tristate_bool("MI_STATE_WARNINGS_STDERR")
        should_print = force if force is not None else (warnings is None)
        if should_print:
            print(f"[mi] state JSON corrupt; quarantined and continued. label={item['label']} path={item['path']}", file=sys.stderr)
        return default
