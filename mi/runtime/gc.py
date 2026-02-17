from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.storage import ensure_dir, now_rfc3339, atomic_write_text


_ARCHIVE_STUB_TYPE = "mi.transcript.archived"


def _is_archive_stub(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            line = f.readline()
    except Exception:
        return False
    s = (line or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        return False
    try:
        obj = json.loads(s)
    except Exception:
        return False
    return isinstance(obj, dict) and str(obj.get("type") or "") == _ARCHIVE_STUB_TYPE


def _sorted_raw_transcripts(dir_path: Path) -> list[Path]:
    if not dir_path.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(dir_path.glob("*.jsonl")):
        if not p.is_file():
            continue
        # Skip stubs (already archived).
        if _is_archive_stub(p):
            continue
        out.append(p)
    return out


@dataclass(frozen=True)
class ArchivePlan:
    kind: str  # hands|mind
    keep: int
    to_archive: list[Path]


def plan_archive_transcripts(*, transcripts_dir: Path, kind: str, keep: int) -> ArchivePlan:
    keep_n = max(0, int(keep))
    raw = _sorted_raw_transcripts(transcripts_dir)
    to_archive = raw[:-keep_n] if keep_n and len(raw) > keep_n else ([] if keep_n else list(raw))
    return ArchivePlan(kind=kind, keep=keep_n, to_archive=to_archive)


def _archive_one(*, src: Path, archive_dir: Path, dry_run: bool) -> dict[str, Any]:
    src = src.resolve()
    if not src.exists() or not src.is_file():
        return {"path": str(src), "status": "skip", "reason": "missing"}
    if _is_archive_stub(src):
        return {"path": str(src), "status": "skip", "reason": "already_stub"}

    dest = archive_dir / (src.name + ".gz")
    if dest.exists():
        return {"path": str(src), "status": "skip", "reason": "archive_exists", "archive_path": str(dest)}

    orig_bytes = int(src.stat().st_size)
    if dry_run:
        return {"path": str(src), "status": "plan", "archive_path": str(dest), "original_bytes": orig_bytes}

    ensure_dir(archive_dir)

    h = hashlib.sha256()
    written = 0
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        while True:
            chunk = f_in.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            written += len(chunk)
            f_out.write(chunk)

    gz_bytes = int(dest.stat().st_size) if dest.exists() else 0
    stub = {
        "type": _ARCHIVE_STUB_TYPE,
        "ts": now_rfc3339(),
        "archived_path": str(dest),
        "original_bytes": written,
        "gzip_bytes": gz_bytes,
        "sha256": h.hexdigest(),
    }
    atomic_write_text(src, json.dumps(stub, sort_keys=True) + "\n")

    return {
        "path": str(src),
        "status": "archived",
        "archive_path": str(dest),
        "original_bytes": written,
        "gzip_bytes": gz_bytes,
    }


def archive_project_transcripts(
    *,
    transcripts_dir: Path,
    keep_hands: int,
    keep_mind: int,
    dry_run: bool,
) -> dict[str, Any]:
    hands_dir = transcripts_dir / "hands"
    mind_dir = transcripts_dir / "mind"

    hands_plan = plan_archive_transcripts(transcripts_dir=hands_dir, kind="hands", keep=keep_hands)
    mind_plan = plan_archive_transcripts(transcripts_dir=mind_dir, kind="mind", keep=keep_mind)

    out: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "hands": {"keep": hands_plan.keep, "planned": len(hands_plan.to_archive), "results": []},
        "mind": {"keep": mind_plan.keep, "planned": len(mind_plan.to_archive), "results": []},
    }

    for plan, subdir in ((hands_plan, "hands"), (mind_plan, "mind")):
        archive_dir = transcripts_dir / subdir / "archive"
        results: list[dict[str, Any]] = []
        for p in plan.to_archive:
            results.append(_archive_one(src=p, archive_dir=archive_dir, dry_run=dry_run))
        out[subdir]["results"] = results

    return out
