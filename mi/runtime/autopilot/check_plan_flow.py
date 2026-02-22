from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .windowing import trim_evidence_window


@dataclass(frozen=True)
class CheckPlanFlowDeps:
    """Dependencies for check-plan query + record orchestration."""

    empty_check_plan: Callable[[], dict[str, Any]]
    evidence_append: Callable[[dict[str, Any]], Any]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    now_ts: Callable[[], str]
    thread_id: str
    plan_min_checks_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]


def append_check_plan_record_with_tracking(
    *,
    batch_id: str,
    checks_obj: Any,
    mind_transcript_ref: str,
    evidence_window: list[dict[str, Any]],
    deps: CheckPlanFlowDeps,
) -> dict[str, Any]:
    """Append check_plan event and keep evidence/segment tracking in sync."""

    obj = checks_obj if isinstance(checks_obj, dict) else deps.empty_check_plan()
    rec = deps.evidence_append(
        {
            "kind": "check_plan",
            "batch_id": str(batch_id),
            "ts": deps.now_ts(),
            "thread_id": deps.thread_id,
            "mind_transcript_ref": str(mind_transcript_ref or ""),
            "checks": obj,
        }
    )
    out = rec if isinstance(rec, dict) else {}
    track = {"kind": "check_plan", "batch_id": str(batch_id), "event_id": out.get("event_id"), **obj}
    evidence_window.append(track)
    trim_evidence_window(evidence_window)
    deps.segment_add(track)
    deps.persist_segment_state()
    return out


def call_plan_min_checks(
    *,
    batch_id: str,
    tag: str,
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    thought_db_context: dict[str, Any],
    recent_evidence: list[dict[str, Any]],
    repo_observation: dict[str, Any],
    notes_on_skipped: str,
    notes_on_error: str,
    deps: CheckPlanFlowDeps,
) -> tuple[dict[str, Any], str, str]:
    """Call plan_min_checks with normalized fallback on skipped/error."""

    checks_prompt = deps.plan_min_checks_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
        recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
    )
    checks_obj, mind_ref, state = deps.mind_call(
        schema_filename="plan_min_checks.json",
        prompt=checks_prompt,
        tag=str(tag or ""),
        batch_id=str(batch_id or ""),
    )
    if checks_obj is None:
        checks_obj = deps.empty_check_plan()
        checks_obj["notes"] = notes_on_skipped if str(state or "") == "skipped" else notes_on_error
    return (
        checks_obj if isinstance(checks_obj, dict) else deps.empty_check_plan(),
        str(mind_ref or ""),
        str(state or ""),
    )


def plan_checks_and_record(
    *,
    batch_id: str,
    tag: str,
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    thought_db_context: dict[str, Any],
    recent_evidence: list[dict[str, Any]],
    repo_observation: dict[str, Any],
    should_plan: bool,
    notes_on_skip: str,
    notes_on_skipped: str,
    notes_on_error: str,
    evidence_window: list[dict[str, Any]],
    postprocess: Any | None,
    deps: CheckPlanFlowDeps,
) -> tuple[dict[str, Any], str, str]:
    """Plan checks (or skip) and always append a check_plan record."""

    if not should_plan:
        checks_obj = deps.empty_check_plan()
        checks_obj["notes"] = str(notes_on_skip or "").strip()
        checks_ref = ""
        state = "skipped"
    else:
        checks_obj, checks_ref, state = call_plan_min_checks(
            batch_id=batch_id,
            tag=tag,
            task=task,
            hands_provider=hands_provider,
            mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
            project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            notes_on_skipped=notes_on_skipped,
            notes_on_error=notes_on_error,
            deps=deps,
        )

    if postprocess and callable(postprocess):
        try:
            out = postprocess(checks_obj, state)
            if isinstance(out, dict):
                checks_obj = out
        except Exception:
            pass

    append_check_plan_record_with_tracking(
        batch_id=batch_id,
        checks_obj=checks_obj,
        mind_transcript_ref=checks_ref,
        evidence_window=evidence_window,
        deps=deps,
    )
    return checks_obj, checks_ref, state
