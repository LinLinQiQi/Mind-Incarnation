from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .interrupts import InterruptConfig


@dataclass(frozen=True)
class MindProviderResult:
    """Normalized Mind provider result payload.

    All Mind providers are expected to return an object with:
    - obj: validated JSON object (provider-local validation)
    - transcript_path: JSONL transcript path for the call (best-effort)
    """

    obj: dict[str, Any]
    transcript_path: Path


class MindProvider(Protocol):
    def call(self, *, schema_filename: str, prompt: str, tag: str) -> MindProviderResult: ...


class MindCallFn(Protocol):
    def __call__(self, *, schema_filename: str, prompt: str, tag: str) -> MindProviderResult: ...


class HandsRunResult(Protocol):
    """Minimal result contract expected by the MI runtime from Hands providers."""

    thread_id: str
    exit_code: int
    events: list[dict[str, Any]]
    raw_transcript_path: Path

    def last_agent_message(self) -> str: ...

    def iter_command_executions(self) -> Iterable[dict[str, Any]]: ...


class HandsExecFn(Protocol):
    def __call__(
        self,
        *,
        prompt: str,
        project_root: Path,
        transcript_path: Path,
        full_auto: bool,
        sandbox: str | None,
        output_schema_path: Path | None,
        interrupt: InterruptConfig | None = None,
    ) -> HandsRunResult: ...


class HandsResumeFn(Protocol):
    def __call__(
        self,
        *,
        thread_id: str,
        prompt: str,
        project_root: Path,
        transcript_path: Path,
        full_auto: bool,
        sandbox: str | None,
        output_schema_path: Path | None,
        interrupt: InterruptConfig | None = None,
    ) -> HandsRunResult: ...

