from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.testless_strategy_flow import (
    TestlessResolutionDeps,
    TestlessStrategyFlowDeps,
    apply_set_testless_strategy_overlay_update,
    canonicalize_tls_and_update_overlay,
    resolve_tls_for_checks,
    sync_tls_overlay_from_thoughtdb,
)


@dataclass(frozen=True)
class TestlessStrategyWiringDeps:
    """Wiring bundle for canonical testless strategy claim + overlay pointer behavior."""

    now_ts: Callable[[], str]
    thread_id_getter: Callable[[], str | None]
    evidence_append: Callable[[dict[str, Any]], Any]
    overlay: dict[str, Any]

    find_testless_strategy_claim: Callable[[str], dict[str, Any] | None]
    parse_testless_strategy_from_claim_text: Callable[[str], str]
    upsert_testless_strategy_claim: Callable[..., str]

    write_overlay: Callable[[dict[str, Any]], None]
    refresh_overlay_refs: Callable[[], None]


@dataclass(frozen=True)
class TestlessResolutionWiringDeps:
    """Wiring bundle for resolving testless strategy during check planning."""

    strategy: TestlessStrategyWiringDeps
    read_user_answer: Callable[[str], str]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    build_thought_db_context_obj: Callable[[str, list[dict[str, Any]]], dict[str, Any]]
    plan_checks_and_record: Callable[..., tuple[dict[str, Any], str, str]]
    plan_checks_and_record2: Callable[..., tuple[dict[str, Any], str, str]]
    empty_check_plan: Callable[[], dict[str, Any]]


def mk_testless_strategy_flow_deps_wired(*, deps: TestlessStrategyWiringDeps) -> TestlessStrategyFlowDeps:
    """Build TestlessStrategyFlowDeps using runner wiring (behavior-preserving)."""

    return TestlessStrategyFlowDeps(
        now_ts=deps.now_ts,
        thread_id=deps.thread_id_getter() if callable(deps.thread_id_getter) else None,
        evidence_append=deps.evidence_append,
        find_testless_strategy_claim=deps.find_testless_strategy_claim,
        parse_testless_strategy_from_claim_text=deps.parse_testless_strategy_from_claim_text,
        upsert_testless_strategy_claim=deps.upsert_testless_strategy_claim,
        write_overlay=deps.write_overlay,
        refresh_overlay_refs=deps.refresh_overlay_refs,
    )


def sync_tls_overlay_from_thoughtdb_wired(*, as_of_ts: str, deps: TestlessStrategyWiringDeps) -> tuple[str, str, bool]:
    """Sync canonical testless strategy claim -> overlay pointer using runner wiring (best-effort)."""

    return sync_tls_overlay_from_thoughtdb(
        overlay=deps.overlay if isinstance(deps.overlay, dict) else {},
        as_of_ts=str(as_of_ts or ""),
        deps=mk_testless_strategy_flow_deps_wired(deps=deps),
    )


def canonicalize_tls_and_update_overlay_wired(
    *,
    strategy_text: str,
    source_event_id: str,
    fallback_batch_id: str,
    overlay_rationale: str,
    overlay_rationale_default: str,
    claim_rationale: str,
    default_rationale: str,
    source: str,
    deps: TestlessStrategyWiringDeps,
) -> str:
    """Canonicalize TLS into Thought DB and mirror an overlay pointer (best-effort)."""

    return canonicalize_tls_and_update_overlay(
        overlay=deps.overlay if isinstance(deps.overlay, dict) else {},
        strategy_text=str(strategy_text or ""),
        source_event_id=str(source_event_id or ""),
        fallback_batch_id=str(fallback_batch_id or ""),
        overlay_rationale=str(overlay_rationale or ""),
        overlay_rationale_default=str(overlay_rationale_default or ""),
        claim_rationale=str(claim_rationale or ""),
        default_rationale=str(default_rationale or ""),
        source=str(source or ""),
        deps=mk_testless_strategy_flow_deps_wired(deps=deps),
    )


def apply_set_testless_strategy_overlay_update_wired(
    *,
    set_tls: Any,
    decide_event_id: str,
    fallback_batch_id: str,
    default_rationale: str,
    source: str,
    deps: TestlessStrategyWiringDeps,
) -> None:
    """Apply update_project_overlay.set_testless_strategy via canonical claim write."""

    apply_set_testless_strategy_overlay_update(
        overlay=deps.overlay if isinstance(deps.overlay, dict) else {},
        set_tls=set_tls,
        decide_event_id=str(decide_event_id or ""),
        fallback_batch_id=str(fallback_batch_id or ""),
        default_rationale=str(default_rationale or ""),
        source=str(source or ""),
        deps=mk_testless_strategy_flow_deps_wired(deps=deps),
    )


def resolve_tls_for_checks_wired(
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
    evidence_window: list[dict[str, Any]],
    deps: TestlessResolutionWiringDeps,
) -> tuple[dict[str, Any], str]:
    """Resolve testless strategy for a check plan using runner wiring (best-effort)."""

    def _canonicalize_tls(**kwargs: Any) -> str:
        return canonicalize_tls_and_update_overlay_wired(
            strategy_text=str(kwargs.get("strategy_text") or ""),
            source_event_id=str(kwargs.get("source_event_id") or ""),
            fallback_batch_id=str(kwargs.get("fallback_batch_id") or ""),
            overlay_rationale=str(kwargs.get("overlay_rationale") or ""),
            overlay_rationale_default=str(kwargs.get("overlay_rationale_default") or ""),
            claim_rationale=str(kwargs.get("claim_rationale") or ""),
            default_rationale=str(kwargs.get("default_rationale") or ""),
            source=str(kwargs.get("source") or ""),
            deps=deps.strategy,
        )

    return resolve_tls_for_checks(
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        hands_last_message=str(hands_last_message or ""),
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        user_input_batch_id=str(user_input_batch_id or ""),
        batch_id_after_testless=str(batch_id_after_testless or ""),
        batch_id_after_tls_claim=str(batch_id_after_tls_claim or ""),
        tag_after_testless=str(tag_after_testless or ""),
        tag_after_tls_claim=str(tag_after_tls_claim or ""),
        notes_prefix=str(notes_prefix or ""),
        source=str(source or ""),
        rationale=str(rationale or ""),
        evidence_window=evidence_window if isinstance(evidence_window, list) else [],
        deps=TestlessResolutionDeps(
            now_ts=deps.strategy.now_ts,
            thread_id=(
                deps.strategy.thread_id_getter() if callable(deps.strategy.thread_id_getter) else None
            ),
            read_user_answer=deps.read_user_answer,
            evidence_append=deps.strategy.evidence_append,
            segment_add=deps.segment_add,
            persist_segment_state=deps.persist_segment_state,
            sync_tls_overlay=lambda ts: sync_tls_overlay_from_thoughtdb_wired(as_of_ts=ts, deps=deps.strategy),
            canonicalize_tls=_canonicalize_tls,
            build_thought_db_context_obj=deps.build_thought_db_context_obj,
            plan_checks_and_record=deps.plan_checks_and_record,
            plan_checks_and_record2=deps.plan_checks_and_record2,
            empty_check_plan=deps.empty_check_plan,
        ),
    )

