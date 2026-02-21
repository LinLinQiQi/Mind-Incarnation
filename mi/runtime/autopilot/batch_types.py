from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class BatchLoopState:
    status: str
    notes: str
    last_batch_id: str = ""
    max_batches_exhausted: bool = False


@dataclass(frozen=True)
class BatchLoopDeps:
    run_single_batch: Callable[[int, str], bool]
