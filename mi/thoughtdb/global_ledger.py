from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..runtime.evidence import EvidenceWriter, new_run_id
from ..core.paths import GlobalPaths
from ..core.storage import iter_jsonl, now_rfc3339


def append_global_event(*, home_dir: Path, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append an event to the global EvidenceLog (append-only).

    This exists so global values/preferences can have stable `event_id` provenance
    without being tied to a specific project EvidenceLog.
    """

    gp = GlobalPaths(home_dir=Path(home_dir).expanduser().resolve())
    evw = EvidenceWriter(path=gp.global_evidence_log_path, run_id=new_run_id("global"))
    rec: dict[str, Any] = {
        "kind": str(kind or "").strip() or "global_event",
        "ts": now_rfc3339(),
        "thread_id": "",
        "payload": payload if isinstance(payload, dict) else {},
    }
    return evw.append(rec)


def iter_global_events(*, home_dir: Path) -> Iterable[dict[str, Any]]:
    gp = GlobalPaths(home_dir=Path(home_dir).expanduser().resolve())
    for obj in iter_jsonl(gp.global_evidence_log_path):
        if isinstance(obj, dict):
            yield obj
