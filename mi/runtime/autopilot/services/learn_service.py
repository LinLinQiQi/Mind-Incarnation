from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class LearnService:
    """Run-end hooks for learn/tighten orchestration."""

    on_run_end_cb: Callable[[], None] | None = None

    def on_run_end(self) -> None:
        if callable(self.on_run_end_cb):
            self.on_run_end_cb()

