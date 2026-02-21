from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .batch_context import BatchExecutionContext
from .run_deps import RunDeps
from .run_state import RunState


@dataclass(frozen=True)
class HandsFlowDeps:
    """Dependencies required by the Hands execution flow."""

    run_deps: RunDeps
    project_root: Path
    transcripts_dir: Path
    cur_provider: str
    no_mi_prompt: bool
    interrupt_cfg: Any
    overlay: dict[str, Any]
    hands_exec: Callable[..., Any]
    hands_resume: Any | None
    write_overlay: Callable[[dict[str, Any]], None]


def _emit_batch_start(*, ctx: BatchExecutionContext, state: RunState, deps: HandsFlowDeps) -> None:
    deps.run_deps.emit_prefixed(
        "[mi]",
        f"batch_start {ctx.batch_id} provider={(deps.cur_provider or 'codex')} mode={('resume' if ctx.use_resume else 'exec')} thread_id={(state.thread_id or '')} transcript={ctx.hands_transcript}",
    )
    if bool(deps.no_mi_prompt):
        return
    deps.run_deps.emit_prefixed("[mi->hands]", "--- light_injection ---")
    deps.run_deps.emit_prefixed("[mi->hands]", ctx.light_injection.rstrip("\n"))
    deps.run_deps.emit_prefixed("[mi->hands]", "--- batch_input ---")
    deps.run_deps.emit_prefixed("[mi->hands]", ctx.batch_input.rstrip("\n"))


def _exec_once(*, ctx: BatchExecutionContext, deps: HandsFlowDeps) -> Any:
    return deps.hands_exec(
        prompt=ctx.hands_prompt,
        project_root=deps.project_root,
        transcript_path=ctx.hands_transcript,
        full_auto=True,
        sandbox=None,
        output_schema_path=None,
        interrupt=deps.interrupt_cfg,
    )


def _resume_once(*, ctx: BatchExecutionContext, state: RunState, deps: HandsFlowDeps) -> Any:
    return deps.hands_resume(
        thread_id=state.thread_id,
        prompt=ctx.hands_prompt,
        project_root=deps.project_root,
        transcript_path=ctx.hands_transcript,
        full_auto=True,
        sandbox=None,
        output_schema_path=None,
        interrupt=deps.interrupt_cfg,
    )


def _maybe_fallback_after_resume_failure(*, ctx: BatchExecutionContext, state: RunState, result: Any, deps: HandsFlowDeps) -> Any:
    if not bool(ctx.attempted_overlay_resume):
        return result
    if int(getattr(result, "exit_code", 0) or 0) == 0:
        return result

    deps.run_deps.evidence_append(
        {
            "kind": "hands_resume_failed",
            "batch_id": ctx.batch_id,
            "ts": deps.run_deps.now_ts(),
            "thread_id": state.thread_id,
            "provider": deps.cur_provider,
            "exit_code": getattr(result, "exit_code", None),
            "notes": "resume failed; falling back to exec",
            "transcript_path": str(ctx.hands_transcript),
        }
    )
    ctx.hands_transcript = deps.transcripts_dir / "hands" / f"{ctx.batch_ts}_b{ctx.batch_idx}_exec_after_resume_fail.jsonl"
    return _exec_once(ctx=ctx, deps=deps)


def _resolve_next_thread_id(*, current_thread_id: str | None, result: Any) -> str | None:
    res_tid = str(getattr(result, "thread_id", "") or "")
    if res_tid and res_tid != "unknown":
        return res_tid
    if current_thread_id is None:
        return res_tid or "unknown"
    return current_thread_id


def _persist_overlay_hands_state(*, state: RunState, deps: HandsFlowDeps) -> None:
    if not state.thread_id or state.thread_id == "unknown":
        return
    hs = deps.overlay.get("hands_state") if isinstance(deps.overlay.get("hands_state"), dict) else {}
    if not isinstance(hs, dict):
        hs = {}
        deps.overlay["hands_state"] = hs
    if deps.cur_provider:
        hs["provider"] = deps.cur_provider
    if str(hs.get("thread_id") or "") != state.thread_id or not str(hs.get("updated_ts") or ""):
        hs["thread_id"] = state.thread_id
        hs["updated_ts"] = deps.run_deps.now_ts()
        deps.write_overlay(deps.overlay)


def run_hands_batch(*, ctx: BatchExecutionContext, state: RunState, deps: HandsFlowDeps) -> tuple[Any, RunState]:
    """Execute one Hands batch and return updated runtime state."""

    _emit_batch_start(ctx=ctx, state=state, deps=deps)

    if not bool(ctx.use_resume):
        result = _exec_once(ctx=ctx, deps=deps)
    else:
        result = _resume_once(ctx=ctx, state=state, deps=deps)
        result = _maybe_fallback_after_resume_failure(ctx=ctx, state=state, result=result, deps=deps)

    next_state = RunState(
        thread_id=_resolve_next_thread_id(current_thread_id=state.thread_id, result=result),
        executed_batches=int(state.executed_batches or 0) + 1,
    )
    deps.run_deps.emit_prefixed(
        "[mi]",
        f"hands_done {ctx.batch_id} exit_code={getattr(result, 'exit_code', None)} thread_id={next_state.thread_id}",
    )

    _persist_overlay_hands_state(state=next_state, deps=deps)
    deps.run_deps.evidence_append(
        {
            "kind": "hands_input",
            "batch_id": ctx.batch_id,
            "ts": ctx.sent_ts,
            "thread_id": next_state.thread_id or result.thread_id,
            "transcript_path": str(ctx.hands_transcript),
            "input": ctx.batch_input,
            "light_injection": ctx.light_injection,
            "prompt_sha256": ctx.prompt_sha256,
        }
    )
    return result, next_state
