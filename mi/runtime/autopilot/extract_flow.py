from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .batch_context import BatchExecutionContext


@dataclass(frozen=True)
class ExtractEvidenceDeps:
    """Callback bundle for extract_evidence phase orchestration."""

    extract_context: Callable[..., tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]]


def run_extract_evidence_phase(
    *,
    batch_idx: int,
    batch_id: str,
    ctx: BatchExecutionContext,
    result: Any,
    repo_obs: dict[str, Any],
    deps: ExtractEvidenceDeps,
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    """Run extract_evidence + thought-context build as one phase."""

    return deps.extract_context(
        batch_idx=batch_idx,
        batch_id=batch_id,
        ctx=ctx,
        result=result,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
    )
