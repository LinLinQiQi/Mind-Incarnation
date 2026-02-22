from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ...thoughtdb.operational_defaults import ensure_operational_defaults_claims_current
from ..autopilot.testless_strategy_flow import MiTestlessStrategyFlowDeps, sync_tls_overlay_from_thoughtdb


@dataclass(frozen=True)
class RunStartSeedsDeps:
    home_dir: Any
    tdb: Any
    overlay: dict[str, Any]
    now_ts: Callable[[], str]
    evidence_append: Callable[[dict[str, Any]], Any]
    mk_testless_strategy_flow_deps: Callable[[], MiTestlessStrategyFlowDeps]
    maybe_cross_project_recall: Callable[..., Any]
    task: str


def run_run_start_seeds(*, deps: RunStartSeedsDeps) -> None:
    """Run small run-start maintenance tasks (behavior-preserving)."""

    # Canonical operational defaults (ask_when_uncertain/refactor_intent) live as global Thought DB preference claims.
    # Runtime config defaults are non-canonical; we only seed missing claims.
    try:
        defaults_sync = ensure_operational_defaults_claims_current(
            home_dir=deps.home_dir,
            tdb=deps.tdb,
            desired_defaults=None,
            mode="seed_missing",
            event_notes="auto_seed_on_run",
            claim_notes_prefix="auto_seed",
        )
    except Exception as e:
        defaults_sync = {
            "ok": False,
            "changed": False,
            "mode": "seed_missing",
            "event_id": "",
            "error": f"{type(e).__name__}: {e}",
        }

    deps.evidence_append(
        {
            "kind": "defaults_claim_sync",
            "batch_id": "b0.defaults_claim_sync",
            "ts": deps.now_ts(),
            "thread_id": "",
            "sync": defaults_sync if isinstance(defaults_sync, dict) else {"ok": False, "error": "invalid result"},
        }
    )

    sync_tls_overlay_from_thoughtdb(
        overlay=deps.overlay,
        as_of_ts=deps.now_ts(),
        deps=deps.mk_testless_strategy_flow_deps(),
    )

    # Seed one conservative recall at run start so later Mind calls can use it without bothering the user.
    if str(deps.task or "").strip():
        deps.maybe_cross_project_recall(batch_id="b0.recall", reason="run_start", query=deps.task)
