from __future__ import annotations

from typing import Any, Callable


def finalize_autopilot_run(
    *,
    checkpoint_enabled: bool,
    executed_batches: int,
    last_batch_id: str,
    max_batches_exhausted: bool,
    status: str,
    checkpoint_runner: Callable[..., None],
    learn_runner: Callable[[], None],
    why_runner: Callable[[], None],
    snapshot_flusher: Callable[[], None],
    state_warning_flusher: Callable[[], None],
) -> None:
    """Run the shared run-end sequence (best-effort, behavior-preserving)."""

    if checkpoint_enabled and executed_batches > 0 and last_batch_id:
        final_hint = "max_batches" if max_batches_exhausted else str(status or "")
        checkpoint_runner(
            batch_id=last_batch_id,
            planned_next_input="",
            status_hint=final_hint,
            note="run_end",
        )

    learn_runner()
    why_runner()

    try:
        snapshot_flusher()
    except Exception:
        pass

    state_warning_flusher()
