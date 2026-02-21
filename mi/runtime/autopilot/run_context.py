from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunSession:
    """Static run wiring shared by orchestration and phase helpers."""

    home: Any | None = None
    project_path: Any | None = None
    project_paths: Any | None = None
    runtime_cfg: dict[str, Any] | None = None
    llm: Any | None = None
    hands_exec: Any | None = None
    hands_resume: Any | None = None
    evw: Any | None = None
    tdb: Any | None = None
    mem: Any | None = None
    wf_registry: Any | None = None
    emit: Any | None = None
    read_user_answer: Any | None = None
    now_ts: Any | None = None


@dataclass
class RunMutableState:
    """Mutable run-level state used by run_engine orchestration."""

    status: str = "not_done"
    notes: str = ""
    last_batch_id: str = ""
    max_batches_exhausted: bool = False
