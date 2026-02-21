from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PreactionPhaseDeps:
    """Callback bundle for pre-action arbitration phase."""

    apply_preactions: Callable[..., tuple[bool | None, dict[str, Any]]]
    empty_auto_answer: Callable[[], dict[str, Any]]


def run_preaction_phase(
    *,
    batch_idx: int,
    hands_last: str,
    repo_obs: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    checks_obj: dict[str, Any],
    auto_answer_obj: dict[str, Any],
    deps: PreactionPhaseDeps,
) -> tuple[bool | None, dict[str, Any]]:
    """Run preaction arbitration and normalize inputs."""

    return deps.apply_preactions(
        batch_idx=batch_idx,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else deps.empty_auto_answer(),
    )
