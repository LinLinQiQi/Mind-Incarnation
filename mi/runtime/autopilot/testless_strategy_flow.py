from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .batch_effects import append_evidence_window


@dataclass(frozen=True)
class MiTestlessStrategyFlowDeps:
    """Dependencies for canonical testless strategy sync/write behavior."""

    now_ts: Callable[[], str]
    thread_id: str
    evidence_append: Callable[[dict[str, Any]], Any]
    find_testless_strategy_claim: Callable[[str], dict[str, Any] | None]
    parse_testless_strategy_from_claim_text: Callable[[str], str]
    upsert_testless_strategy_claim: Callable[..., str]
    write_overlay: Callable[[dict[str, Any]], None]
    refresh_overlay_refs: Callable[[], None]


@dataclass(frozen=True)
class MiTestlessResolutionDeps:
    """Dependencies for resolving testless strategy during check planning."""

    now_ts: Callable[[], str]
    thread_id: str
    read_user_answer: Callable[[str], str]
    evidence_append: Callable[[dict[str, Any]], Any]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    sync_tls_overlay: Callable[[str], tuple[str, str, bool]]
    canonicalize_tls: Callable[..., str]
    build_thought_db_context_obj: Callable[[str, list[dict[str, Any]]], dict[str, Any]]
    plan_checks_and_record: Callable[..., tuple[dict[str, Any], str, str]]
    plan_checks_and_record2: Callable[..., tuple[dict[str, Any], str, str]]
    empty_check_plan: Callable[[], dict[str, Any]]


def sync_tls_overlay_from_thoughtdb(
    *,
    overlay: dict[str, Any],
    as_of_ts: str,
    deps: MiTestlessStrategyFlowDeps,
) -> tuple[str, str, bool]:
    """Sync canonical testless strategy claim to overlay pointer (best-effort)."""

    tls = overlay.get("testless_verification_strategy") if isinstance(overlay, dict) else None
    tls_chosen_once = bool(tls.get("chosen_once", False)) if isinstance(tls, dict) else False

    tls_claim = deps.find_testless_strategy_claim(str(as_of_ts or ""))
    tls_claim_strategy = ""
    tls_claim_id = ""
    if isinstance(tls_claim, dict):
        tls_claim_id = str(tls_claim.get("claim_id") or "").strip()
        tls_claim_strategy = deps.parse_testless_strategy_from_claim_text(str(tls_claim.get("text") or ""))

    if tls_claim_strategy:
        tls_chosen_once = True
        cur_cid = str(tls.get("claim_id") or "").strip() if isinstance(tls, dict) else ""
        if tls_claim_id and cur_cid.strip() != tls_claim_id.strip():
            overlay.setdefault("testless_verification_strategy", {})
            overlay["testless_verification_strategy"] = {
                "chosen_once": True,
                "claim_id": tls_claim_id,
                "rationale": f"derived from Thought DB {tls_claim_id}",
            }
            deps.write_overlay(overlay)
            deps.refresh_overlay_refs()

    return tls_claim_strategy, tls_claim_id, tls_chosen_once


def canonicalize_tls_and_update_overlay(
    *,
    overlay: dict[str, Any],
    strategy_text: str,
    source_event_id: str,
    fallback_batch_id: str,
    overlay_rationale: str,
    overlay_rationale_default: str,
    claim_rationale: str,
    default_rationale: str,
    source: str,
    deps: MiTestlessStrategyFlowDeps,
) -> str:
    """Canonicalize testless strategy as claim and mirror overlay pointer."""

    strategy = str(strategy_text or "").strip()
    if not strategy:
        return ""

    src_eid = str(source_event_id or "").strip()
    if not src_eid:
        rec = deps.evidence_append(
            {
                "kind": "testless_strategy_set",
                "batch_id": str(fallback_batch_id or "").strip(),
                "ts": deps.now_ts(),
                "thread_id": deps.thread_id,
                "strategy": strategy,
                "rationale": str(claim_rationale or default_rationale or "").strip(),
            }
        )
        src_eid = str((rec if isinstance(rec, dict) else {}).get("event_id") or "").strip()

    tls_cid = deps.upsert_testless_strategy_claim(
        strategy_text=strategy,
        source_event_id=src_eid,
        source=str(source or "").strip(),
        rationale=str(claim_rationale or default_rationale or "").strip(),
    )

    overlay.setdefault("testless_verification_strategy", {})
    overlay["testless_verification_strategy"] = {
        "chosen_once": True,
        "claim_id": tls_cid,
        "rationale": (
            f"{overlay_rationale} (canonical claim {tls_cid})"
            if tls_cid
            else str(overlay_rationale_default or "").strip()
        ),
    }
    deps.write_overlay(overlay)
    deps.refresh_overlay_refs()
    return tls_cid


def apply_set_testless_strategy_overlay_update(
    *,
    overlay: dict[str, Any],
    set_tls: Any,
    decide_event_id: str,
    fallback_batch_id: str,
    default_rationale: str,
    source: str,
    deps: MiTestlessStrategyFlowDeps,
) -> None:
    """Apply update_project_overlay.set_testless_strategy via canonical claim write."""

    if not isinstance(set_tls, dict):
        return

    strategy = str(set_tls.get("strategy") or "").strip()
    rationale = str(set_tls.get("rationale") or "").strip()
    if not strategy:
        return

    canonicalize_tls_and_update_overlay(
        overlay=overlay if isinstance(overlay, dict) else {},
        strategy_text=strategy,
        source_event_id=str(decide_event_id or "").strip(),
        fallback_batch_id=str(fallback_batch_id or "").strip(),
        overlay_rationale=rationale,
        overlay_rationale_default=rationale,
        claim_rationale=rationale or str(default_rationale or "").strip(),
        default_rationale=str(default_rationale or "").strip(),
        source=str(source or "").strip(),
        deps=deps,
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
    evidence_window: list[dict[str, Any]],
    deps: MiTestlessResolutionDeps,
) -> tuple[dict[str, Any], str]:
    """Resolve testless strategy for a check plan (ask once + re-plan; best-effort)."""

    checks = checks_obj if isinstance(checks_obj, dict) else {}

    def _notes_label(label: str) -> str:
        n = str(notes_prefix or "").strip()
        if n:
            return n + " " + str(label or "").strip()
        return str(label or "").strip()

    tls_claim_strategy, _, tls_chosen_once = deps.sync_tls_overlay(str(deps.now_ts() or ""))

    needs_tls = bool(checks.get("needs_testless_strategy", False)) if isinstance(checks, dict) else False
    if needs_tls and not tls_chosen_once:
        q = str(checks.get("testless_strategy_question") or "").strip()
        if not q:
            q = "This project appears to have no tests. What testless verification strategy should MI use for this project (one-time)?"
        answer = deps.read_user_answer(q)
        if not answer:
            return checks, "user did not provide required input"

        ui = deps.evidence_append(
            {
                "kind": "user_input",
                "batch_id": str(user_input_batch_id),
                "ts": deps.now_ts(),
                "thread_id": deps.thread_id,
                "question": q,
                "answer": answer,
            }
        )
        ui_obj = ui if isinstance(ui, dict) else {}
        append_evidence_window(
            evidence_window,
            {
                "kind": "user_input",
                "batch_id": str(user_input_batch_id),
                "event_id": ui_obj.get("event_id"),
                "question": q,
                "answer": answer,
            },
            limit=8,
        )
        deps.segment_add(ui_obj)
        deps.persist_segment_state()

        deps.canonicalize_tls(
            strategy_text=str(answer or "").strip(),
            source_event_id=str(ui_obj.get("event_id") or "").strip(),
            fallback_batch_id=str(user_input_batch_id),
            overlay_rationale="user provided",
            overlay_rationale_default="user provided testless verification strategy",
            claim_rationale=rationale,
            default_rationale=rationale,
            source=source,
        )

        tdb_ctx2_obj = deps.build_thought_db_context_obj(hands_last_message, evidence_window)
        checks_obj2, _, _ = deps.plan_checks_and_record(
            batch_id=batch_id_after_testless,
            tag=tag_after_testless,
            thought_db_context=tdb_ctx2_obj if isinstance(tdb_ctx2_obj, dict) else {},
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            should_plan=True,
            notes_on_skip="",
            notes_on_skipped=f"skipped: mind_circuit_open (plan_min_checks {_notes_label('after_testless')})",
            notes_on_error=f"mind_error: plan_min_checks({_notes_label('after_testless')}) failed; see EvidenceLog kind=mind_error",
        )
        checks = checks_obj2 if isinstance(checks_obj2, dict) else deps.empty_check_plan()

        tls_claim_strategy, _, tls_chosen_once = deps.sync_tls_overlay(str(deps.now_ts() or ""))

    needs_tls2 = bool(checks.get("needs_testless_strategy", False)) if isinstance(checks, dict) else False
    if needs_tls2 and tls_claim_strategy:
        tdb_ctx_tls_obj = deps.build_thought_db_context_obj(hands_last_message, evidence_window)
        notes_on_skipped = f"skipped: mind_circuit_open (plan_min_checks {_notes_label('after_tls_claim')})"
        notes_on_error = f"mind_error: plan_min_checks({_notes_label('after_tls_claim')}) failed; using Thought DB strategy"

        def _postprocess_after_tls_claim(obj: dict[str, Any], state: str) -> dict[str, Any]:
            if str(state or "") != "ok":
                checks["needs_testless_strategy"] = False
                checks["testless_strategy_question"] = ""
                base_note = str(checks.get("notes") or "").strip()
                extra = notes_on_skipped if str(state or "") == "skipped" else notes_on_error
                checks["notes"] = (base_note + "; " + extra).strip("; ").strip()
                return checks
            return obj if isinstance(obj, dict) else deps.empty_check_plan()

        checks_obj3, _, _ = deps.plan_checks_and_record2(
            batch_id=batch_id_after_tls_claim,
            tag=tag_after_tls_claim,
            thought_db_context=tdb_ctx_tls_obj if isinstance(tdb_ctx_tls_obj, dict) else {},
            repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
            should_plan=True,
            notes_on_skip="",
            notes_on_skipped=notes_on_skipped,
            notes_on_error=notes_on_error,
            postprocess=_postprocess_after_tls_claim,
        )
        checks = checks_obj3 if isinstance(checks_obj3, dict) else deps.empty_check_plan()

    return checks, ""
