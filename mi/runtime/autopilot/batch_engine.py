from __future__ import annotations

from .batch_types import BatchLoopDeps, BatchLoopState


def run_batch_loop(*, max_batches: int, state: BatchLoopState, deps: BatchLoopDeps) -> BatchLoopState:
    """Run the batch loop skeleton and delegate each batch to the runner callback."""

    for batch_idx in range(max_batches):
        batch_id = f"b{batch_idx}"
        state.last_batch_id = batch_id
        if not bool(deps.run_single_batch(batch_idx, batch_id)):
            return state

    state.max_batches_exhausted = True
    state.status = "blocked"
    state.notes = f"reached max_batches={max_batches}"
    return state
