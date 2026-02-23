from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...core.paths import ProjectPaths
from ...memory.facade import MemoryFacade
from ...providers.types import HandsExecFn, HandsResumeFn, MindProvider
from ...runtime.evidence import EvidenceWriter
from ...thoughtdb import ThoughtDbStore
from ...workflows import WorkflowRegistry


@dataclass(frozen=True)
class RunSession:
    """Static run wiring shared by orchestration and phase helpers."""

    home: Path
    project_path: Path
    project_paths: ProjectPaths
    runtime_cfg: dict[str, Any]
    llm: MindProvider
    hands_exec: HandsExecFn
    hands_resume: HandsResumeFn | None
    evw: EvidenceWriter
    tdb: ThoughtDbStore
    mem: MemoryFacade
    wf_registry: WorkflowRegistry
    emit: Callable[[str, str], None]
    read_user_answer: Callable[[str], str]
    now_ts: Callable[[], str]


@dataclass
class RunMutableState:
    """Mutable run-level state used by run_engine orchestration."""

    status: str = "not_done"
    notes: str = ""
    last_batch_id: str = ""
    max_batches_exhausted: bool = False
