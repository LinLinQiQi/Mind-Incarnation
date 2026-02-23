from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import wiring as W
from . import autopilot as AP
from .autopilot import services as APS
from . import prompts as P


@dataclass(frozen=True)
class TestlessWiringBundle:
    """Wiring helpers for the V1 "testless strategy" path (behavior-preserving)."""

    tls_strategy_wiring: W.MiTestlessStrategyWiringDeps
    check_plan_wiring: W.CheckPlanWiringDeps
    tls_resolution_wiring: W.MiTestlessResolutionWiringDeps
    plan_checks_and_record: Callable[..., tuple[dict[str, Any], str, str]]
    resolve_tls_for_checks: Callable[..., tuple[dict[str, Any], str]]
    apply_set_testless_strategy_overlay_update: Callable[..., None]


def build_testless_wiring_bundle(
    *,
    project_id: str,
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    overlay: dict[str, Any],
    evidence_window: list[dict[str, Any]],
    tdb: Any,
    now_ts: Callable[[], str],
    thread_id_getter: Callable[[], str | None],
    evidence_append: Callable[[dict[str, Any]], Any],
    refresh_overlay_refs: Callable[[], None],
    write_project_overlay: Callable[[dict[str, Any]], None],
    segment_add: Callable[[dict[str, Any]], None],
    persist_segment_state: Callable[[], None],
    read_user_answer: Callable[[str], str],
    build_thought_db_context_obj: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    mind_call: Callable[..., Any],
    empty_check_plan: Callable[[], dict[str, Any]],
) -> TestlessWiringBundle:
    """Build testless-strategy related wiring + convenience wrappers.

    This exists to keep `runner_wiring_builder.py` smaller and reduce drift risk.
    """

    def _parse_testless_strategy_from_claim_text(text: str) -> str:
        return APS.parse_testless_strategy_from_claim_text(text)

    def _find_testless_strategy_claim(*, as_of_ts: str) -> dict[str, Any] | None:
        return APS.find_testless_strategy_claim(tdb=tdb, as_of_ts=as_of_ts)

    def _upsert_testless_strategy_claim(*, strategy_text: str, source_event_id: str, source: str, rationale: str) -> str:
        return APS.upsert_testless_strategy_claim(
            tdb=tdb,
            project_id=project_id,
            strategy_text=strategy_text,
            source_event_id=source_event_id,
            source=source,
            rationale=rationale,
        )

    tls_strategy_wiring = W.MiTestlessStrategyWiringDeps(
        now_ts=now_ts,
        thread_id_getter=lambda: thread_id_getter(),
        evidence_append=evidence_append,
        overlay=overlay if isinstance(overlay, dict) else {},
        find_testless_strategy_claim=lambda ts: _find_testless_strategy_claim(as_of_ts=ts),
        parse_testless_strategy_from_claim_text=_parse_testless_strategy_from_claim_text,
        upsert_testless_strategy_claim=_upsert_testless_strategy_claim,
        write_overlay=lambda obj: write_project_overlay(obj),
        refresh_overlay_refs=refresh_overlay_refs,
    )

    check_plan_wiring = W.CheckPlanWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        evidence_window=evidence_window,
        thread_id_getter=lambda: thread_id_getter(),
        now_ts=now_ts,
        evidence_append=evidence_append,
        segment_add=segment_add,
        persist_segment_state=persist_segment_state,
        plan_min_checks_prompt_builder=P.plan_min_checks_prompt,
        mind_call=mind_call,
        empty_check_plan=AP._empty_check_plan,
    )

    def plan_checks_and_record(
        *,
        batch_id: str,
        tag: str,
        thought_db_context: dict[str, Any],
        repo_observation: dict[str, Any],
        should_plan: bool,
        notes_on_skip: str,
        notes_on_skipped: str,
        notes_on_error: str,
        postprocess: Any | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """Plan minimal checks and always record a check_plan event (best-effort)."""

        return W.plan_checks_and_record_wired(
            batch_id=batch_id,
            tag=tag,
            thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            should_plan=bool(should_plan),
            notes_on_skip=notes_on_skip,
            notes_on_skipped=notes_on_skipped,
            notes_on_error=notes_on_error,
            postprocess=postprocess,
            deps=check_plan_wiring,
        )

    tls_resolution_wiring = W.MiTestlessResolutionWiringDeps(
        strategy=tls_strategy_wiring,
        read_user_answer=read_user_answer,
        segment_add=lambda item: segment_add(item if isinstance(item, dict) else {}),
        persist_segment_state=persist_segment_state,
        build_thought_db_context_obj=build_thought_db_context_obj,
        plan_checks_and_record=lambda **kwargs: plan_checks_and_record(
            batch_id=str(kwargs.get("batch_id") or ""),
            tag=str(kwargs.get("tag") or ""),
            thought_db_context=(kwargs.get("thought_db_context") if isinstance(kwargs.get("thought_db_context"), dict) else {}),
            repo_observation=(kwargs.get("repo_observation") if isinstance(kwargs.get("repo_observation"), dict) else {}),
            should_plan=bool(kwargs.get("should_plan")),
            notes_on_skip=str(kwargs.get("notes_on_skip") or ""),
            notes_on_skipped=str(kwargs.get("notes_on_skipped") or ""),
            notes_on_error=str(kwargs.get("notes_on_error") or ""),
            postprocess=None,
        ),
        plan_checks_and_record2=lambda **kwargs: plan_checks_and_record(
            batch_id=str(kwargs.get("batch_id") or ""),
            tag=str(kwargs.get("tag") or ""),
            thought_db_context=(kwargs.get("thought_db_context") if isinstance(kwargs.get("thought_db_context"), dict) else {}),
            repo_observation=(kwargs.get("repo_observation") if isinstance(kwargs.get("repo_observation"), dict) else {}),
            should_plan=bool(kwargs.get("should_plan")),
            notes_on_skip=str(kwargs.get("notes_on_skip") or ""),
            notes_on_skipped=str(kwargs.get("notes_on_skipped") or ""),
            notes_on_error=str(kwargs.get("notes_on_error") or ""),
            postprocess=kwargs.get("postprocess"),
        ),
        empty_check_plan=empty_check_plan,
    )

    def resolve_tls_for_checks(
        *,
        checks_obj: dict[str, Any],
        hands_last_message: str,
        repo_observation: dict[str, Any],
        user_input_batch_id: str,
        batch_id_after_testless: str,
        batch_id_after_tls_claim: str,
        tag_after_testless: str,
        tag_after_tls_claim: str,
        notes_prefix: str,
        source: str,
        rationale: str,
    ) -> tuple[dict[str, Any], str]:
        """Resolve testless strategy for a check plan (ask once + re-plan; best-effort).

        Returns: (final_checks_obj, block_reason). block_reason=="" means OK.
        """

        return W.resolve_tls_for_checks_wired(
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            hands_last_message=hands_last_message,
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            user_input_batch_id=user_input_batch_id,
            batch_id_after_testless=batch_id_after_testless,
            batch_id_after_tls_claim=batch_id_after_tls_claim,
            tag_after_testless=tag_after_testless,
            tag_after_tls_claim=tag_after_tls_claim,
            notes_prefix=notes_prefix,
            source=source,
            rationale=rationale,
            evidence_window=evidence_window,
            deps=tls_resolution_wiring,
        )

    def apply_set_testless_strategy_overlay_update(
        *,
        set_tls: Any,
        decide_event_id: str,
        fallback_batch_id: str,
        default_rationale: str,
        source: str,
    ) -> None:
        """Apply update_project_overlay.set_testless_strategy (canonicalized via Thought DB)."""

        W.apply_set_testless_strategy_overlay_update_wired(
            set_tls=set_tls,
            decide_event_id=decide_event_id,
            fallback_batch_id=fallback_batch_id,
            default_rationale=default_rationale,
            source=source,
            deps=tls_strategy_wiring,
        )

    return TestlessWiringBundle(
        tls_strategy_wiring=tls_strategy_wiring,
        check_plan_wiring=check_plan_wiring,
        tls_resolution_wiring=tls_resolution_wiring,
        plan_checks_and_record=plan_checks_and_record,
        resolve_tls_for_checks=resolve_tls_for_checks,
        apply_set_testless_strategy_overlay_update=apply_set_testless_strategy_overlay_update,
    )
