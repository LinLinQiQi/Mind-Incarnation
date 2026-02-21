from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TestlessStrategyFlowDeps:
    """Dependencies for canonical testless strategy sync/write behavior."""

    now_ts: Callable[[], str]
    thread_id: str
    evidence_append: Callable[[dict[str, Any]], Any]
    find_testless_strategy_claim: Callable[[str], dict[str, Any] | None]
    parse_testless_strategy_from_claim_text: Callable[[str], str]
    upsert_testless_strategy_claim: Callable[..., str]
    write_overlay: Callable[[dict[str, Any]], None]
    refresh_overlay_refs: Callable[[], None]


def sync_tls_overlay_from_thoughtdb(
    *,
    overlay: dict[str, Any],
    as_of_ts: str,
    deps: TestlessStrategyFlowDeps,
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
    deps: TestlessStrategyFlowDeps,
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
    deps: TestlessStrategyFlowDeps,
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
