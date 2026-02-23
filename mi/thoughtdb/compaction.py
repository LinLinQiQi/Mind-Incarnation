from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from ..core.storage import ensure_dir, filename_safe_ts, iter_jsonl, now_rfc3339


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _archive_gzip(*, src: Path, dest_gz: Path, dry_run: bool) -> dict[str, Any]:
    src = Path(src).expanduser().resolve()
    dest_gz = Path(dest_gz).expanduser().resolve()

    if not src.exists() or not src.is_file():
        return {"path": str(src), "status": "skip", "reason": "missing"}
    if dest_gz.exists():
        return {"path": str(src), "status": "skip", "reason": "archive_exists", "archive_path": str(dest_gz)}

    orig_bytes = int(src.stat().st_size)
    if dry_run:
        return {"path": str(src), "status": "plan", "archive_path": str(dest_gz), "original_bytes": orig_bytes}

    ensure_dir(dest_gz.parent)
    h = hashlib.sha256()
    written = 0
    with src.open("rb") as f_in, gzip.open(dest_gz, "wb") as f_out:
        while True:
            chunk = f_in.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            written += len(chunk)
            f_out.write(chunk)
    gz_bytes = int(dest_gz.stat().st_size) if dest_gz.exists() else 0
    return {
        "path": str(src),
        "status": "archived",
        "archive_path": str(dest_gz),
        "original_bytes": written,
        "gzip_bytes": gz_bytes,
        "sha256": h.hexdigest(),
    }


def _atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
    path = Path(path).expanduser().resolve()

    n = 0
    out_bytes = 0

    # Precompute a compacted byte size estimate in dry-run without writing a file.
    if dry_run:
        for obj in rows:
            line = json.dumps(obj, sort_keys=True) + "\n"
            out_bytes += len(line.encode("utf-8"))
            n += 1
        return {"path": str(path), "status": "plan", "lines": n, "bytes": out_bytes}

    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        for obj in rows:
            line = json.dumps(obj, sort_keys=True) + "\n"
            out_bytes += len(line.encode("utf-8"))
            f.write(line)
            n += 1
    tmp.replace(path)
    return {"path": str(path), "status": "written", "lines": n, "bytes": out_bytes}


def _compact_claims_jsonl(*, path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (rows, stats) for a compacted claims.jsonl (best-effort, strict kinds)."""

    claims_by_id: dict[str, dict[str, Any]] = {}
    retract_last: dict[str, tuple[int, dict[str, Any]]] = {}
    unknown: set[str] = set()

    total = 0
    for idx, obj in enumerate(iter_jsonl(path)):
        total += 1
        if not isinstance(obj, dict):
            continue
        k = str(obj.get("kind") or "").strip()
        if k == "claim":
            cid = str(obj.get("claim_id") or "").strip()
            if cid:
                claims_by_id[cid] = obj
        elif k == "claim_retract":
            cid = str(obj.get("claim_id") or "").strip()
            if cid:
                retract_last[cid] = (idx, obj)
        elif k:
            unknown.add(k)

    if unknown:
        raise ValueError(f"unknown claims record kinds: {sorted(unknown)}")

    creates = list(claims_by_id.values())
    creates.sort(key=lambda x: (str(x.get("asserted_ts") or "").strip(), str(x.get("claim_id") or "").strip()))

    retracts = [obj for _idx, obj in sorted(retract_last.values(), key=lambda t: t[0])]

    rows = [*creates, *retracts]
    stats = {
        "input_lines": total,
        "output_lines": len(rows),
        "claims": len(claims_by_id),
        "retracts": len(retract_last),
    }
    return rows, stats


def _compact_nodes_jsonl(*, path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (rows, stats) for a compacted nodes.jsonl (best-effort, strict kinds)."""

    nodes_by_id: dict[str, dict[str, Any]] = {}
    retract_last: dict[str, tuple[int, dict[str, Any]]] = {}
    unknown: set[str] = set()

    total = 0
    for idx, obj in enumerate(iter_jsonl(path)):
        total += 1
        if not isinstance(obj, dict):
            continue
        k = str(obj.get("kind") or "").strip()
        if k == "node":
            nid = str(obj.get("node_id") or "").strip()
            if nid:
                nodes_by_id[nid] = obj
        elif k == "node_retract":
            nid = str(obj.get("node_id") or "").strip()
            if nid:
                retract_last[nid] = (idx, obj)
        elif k:
            unknown.add(k)

    if unknown:
        raise ValueError(f"unknown nodes record kinds: {sorted(unknown)}")

    creates = list(nodes_by_id.values())
    creates.sort(key=lambda x: (str(x.get("asserted_ts") or "").strip(), str(x.get("node_id") or "").strip()))

    retracts = [obj for _idx, obj in sorted(retract_last.values(), key=lambda t: t[0])]

    rows = [*creates, *retracts]
    stats = {
        "input_lines": total,
        "output_lines": len(rows),
        "nodes": len(nodes_by_id),
        "retracts": len(retract_last),
    }
    return rows, stats


def _edge_key(obj: dict[str, Any], *, idx: int) -> str:
    et = str(obj.get("edge_type") or "").strip()
    frm = str(obj.get("from_id") or "").strip()
    to = str(obj.get("to_id") or "").strip()
    if et and frm and to:
        return f"{et}|{frm}|{to}"
    edge_id = str(obj.get("edge_id") or "").strip()
    if edge_id:
        return f"edge_id:{edge_id}"
    return f"idx:{idx}"


def _compact_edges_jsonl(*, path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (rows, stats) for a compacted edges.jsonl (keep last per key, preserve last-occurrence order)."""

    unknown: set[str] = set()
    last_index: dict[str, int] = {}

    total = 0
    for idx, obj in enumerate(iter_jsonl(path)):
        total += 1
        if not isinstance(obj, dict):
            continue
        k = str(obj.get("kind") or "").strip()
        if k != "edge":
            if k:
                unknown.add(k)
            continue
        key = _edge_key(obj, idx=idx)
        last_index[key] = idx

    if unknown:
        raise ValueError(f"unknown edges record kinds: {sorted(unknown)}")

    out: list[dict[str, Any]] = []
    kept = 0
    for idx, obj in enumerate(iter_jsonl(path)):
        if not isinstance(obj, dict):
            continue
        if str(obj.get("kind") or "").strip() != "edge":
            continue
        key = _edge_key(obj, idx=idx)
        if last_index.get(key) == idx:
            out.append(obj)
            kept += 1

    stats = {
        "input_lines": total,
        "output_lines": kept,
        "unique_keys": len(last_index),
    }
    return out, stats


def compact_thoughtdb_dir(
    *,
    thoughtdb_dir: Path,
    snapshot_path: Path,
    dry_run: bool,
) -> dict[str, Any]:
    """Compact a Thought DB directory (claims/edges/nodes) with archival backup (best-effort).

    - Archives the current JSONL files under thoughtdb_dir/archive/<ts>/ as .gz.
    - Rewrites claims/edges/nodes JSONL files into a compacted form.
    - Deletes the persisted view snapshot so it can be rebuilt from compacted files.
    """

    tdir = Path(thoughtdb_dir).expanduser().resolve()
    claims_path = tdir / "claims.jsonl"
    edges_path = tdir / "edges.jsonl"
    nodes_path = tdir / "nodes.jsonl"

    stamp = filename_safe_ts(now_rfc3339())
    archive_dir = tdir / "archive" / stamp
    out: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "thoughtdb_dir": str(tdir),
        "archive_dir": str(archive_dir),
        "files": {},
        "snapshot": {"path": str(snapshot_path), "deleted": False},
    }

    claims_rows, claims_stats = _compact_claims_jsonl(path=claims_path)
    edges_rows, edges_stats = _compact_edges_jsonl(path=edges_path)
    nodes_rows, nodes_stats = _compact_nodes_jsonl(path=nodes_path)

    # Archive current files first (safe, reversible).
    out["files"]["claims"] = {
        "archive": _archive_gzip(src=claims_path, dest_gz=archive_dir / "claims.jsonl.gz", dry_run=dry_run),
        "compact_stats": claims_stats,
    }
    out["files"]["edges"] = {
        "archive": _archive_gzip(src=edges_path, dest_gz=archive_dir / "edges.jsonl.gz", dry_run=dry_run),
        "compact_stats": edges_stats,
    }
    out["files"]["nodes"] = {
        "archive": _archive_gzip(src=nodes_path, dest_gz=archive_dir / "nodes.jsonl.gz", dry_run=dry_run),
        "compact_stats": nodes_stats,
    }

    # Write compacted files.
    out["files"]["claims"]["write"] = _atomic_write_jsonl(claims_path, claims_rows, dry_run=dry_run)
    out["files"]["edges"]["write"] = _atomic_write_jsonl(edges_path, edges_rows, dry_run=dry_run)
    out["files"]["nodes"]["write"] = _atomic_write_jsonl(nodes_path, nodes_rows, dry_run=dry_run)

    # Snapshot invalidates when metas change; delete it explicitly to force rebuild.
    snap = Path(snapshot_path).expanduser().resolve()
    if snap.exists():
        if dry_run:
            out["snapshot"]["deleted"] = True
            out["snapshot"]["status"] = "plan_delete"
        else:
            try:
                snap.unlink()
                out["snapshot"]["deleted"] = True
                out["snapshot"]["status"] = "deleted"
            except Exception as e:
                out["snapshot"]["status"] = f"delete_failed:{type(e).__name__}"

    if not dry_run:
        # Emit a small manifest for audit/debug.
        man = {
            "kind": "mi.thoughtdb.compaction_manifest",
            "version": "v1",
            "ts": now_rfc3339(),
            "thoughtdb_dir": str(tdir),
            "files": {
                "claims": {"path": str(claims_path), "sha256": (_sha256_file(claims_path) if claims_path.exists() else "")},
                "edges": {"path": str(edges_path), "sha256": (_sha256_file(edges_path) if edges_path.exists() else "")},
                "nodes": {"path": str(nodes_path), "sha256": (_sha256_file(nodes_path) if nodes_path.exists() else "")},
            },
        }
        try:
            ensure_dir(archive_dir)
            (archive_dir / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            out["manifest_path"] = str(archive_dir / "manifest.json")
        except Exception:
            pass

    return out


__all__ = ["compact_thoughtdb_dir"]

