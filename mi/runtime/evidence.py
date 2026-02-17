from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.storage import append_jsonl, now_rfc3339


def new_run_id(prefix: str = "run") -> str:
    p = (prefix or "run").strip() or "run"
    return f"{p}_{time.time_ns()}_{secrets.token_hex(4)}"


@dataclass
class EvidenceWriter:
    """Append-only EvidenceLog writer with stable event identifiers.

    - event_id: unique within the log (derived from run_id + seq)
    - run_id: unique per writer/session (e.g., one `mi run` invocation)
    - seq: monotonically increasing within the run_id
    """

    path: Path
    run_id: str
    seq: int = 0

    def append(self, rec: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        obj = dict(rec) if isinstance(rec, dict) else {"value": rec}

        # Best-effort: ensure ts exists.
        ts = str(obj.get("ts") or "").strip()
        if not ts:
            obj["ts"] = now_rfc3339()

        obj["run_id"] = str(self.run_id or "").strip() or new_run_id("run")
        obj["seq"] = int(self.seq)
        obj["event_id"] = f"ev_{obj['run_id']}_{int(self.seq):06d}"

        append_jsonl(self.path, obj)
        return obj
