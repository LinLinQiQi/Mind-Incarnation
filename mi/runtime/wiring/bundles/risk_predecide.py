from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mi.runtime import autopilot as AP
from mi.runtime import prompts as P
import mi.runtime.wiring as W
from mi.runtime.autopilot import risk_predecide as RP


@dataclass(frozen=True)
class RiskPredecideWiringBundle:
    """Runner wiring bundle for the risk predecide phase (behavior-preserving)."""

    detect_risk_signals: Callable[..., list[str]]
    judge_and_handle_risk: Callable[..., bool | None]


def build_risk_predecide_wiring_bundle(
    *,
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    overlay: dict[str, Any],
    maybe_cross_project_recall: Callable[..., None],
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]],
    evidence_window: list[dict[str, Any]],
    evidence_append: Callable[[dict[str, Any]], Any],
    segment_add: Callable[[dict[str, Any]], None],
    persist_segment_state: Callable[[], None],
    now_ts: Callable[[], str],
    thread_id_getter: Callable[[], str],
    runtime_cfg: dict[str, Any],
    read_user_answer: Callable[[str], str],
    set_status: Callable[[str], None],
    set_notes: Callable[[str], None],
    handle_learn_suggested: Callable[..., list[str]],
) -> RiskPredecideWiringBundle:
    """Build risk predecide wiring closures used in the predecide phase."""

    def detect_risk_signals(*, result: Any, ctx: AP.BatchExecutionContext) -> list[str]:
        """Detect risk signals from structured events, then transcript fallback when needed."""

        risk_signals = AP._detect_risk_signals(result)
        if not risk_signals and not (isinstance(getattr(result, "events", None), list) and result.events):
            risk_signals = AP._detect_risk_signals_from_transcript(ctx.hands_transcript)
        return [str(x) for x in risk_signals if str(x).strip()]

    risk_judge_wiring = W.RiskJudgeWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        maybe_cross_project_recall=maybe_cross_project_recall,
        risk_judge_prompt_builder=P.risk_judge_prompt,
        mind_call=mind_call,
        build_risk_fallback=AP.build_risk_fallback,
    )

    risk_event_wiring = W.RiskEventRecordWiringDeps(
        evidence_window=evidence_window,
        evidence_append=evidence_append,
        append_window=AP.append_evidence_window,
        segment_add=segment_add,
        persist_segment_state=persist_segment_state,
        now_ts=now_ts,
        thread_id_getter=thread_id_getter,
    )

    def _query_risk(
        *,
        batch_idx: int,
        batch_id: str,
        risk_signals: list[str],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """Run recall + risk_judge and normalize fallback output."""

        return W.query_risk_judge_wired(
            batch_idx=batch_idx,
            batch_id=batch_id,
            risk_signals=risk_signals,
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=risk_judge_wiring,
        )

    def _record_risk_event(
        *,
        batch_idx: int,
        risk_signals: list[str],
        risk_obj: dict[str, Any],
        risk_mind_ref: str,
    ) -> dict[str, Any]:
        """Persist risk event to EvidenceLog + segment + evidence window."""

        return W.append_risk_event_wired(
            batch_idx=batch_idx,
            risk_signals=risk_signals,
            risk_obj=risk_obj if isinstance(risk_obj, dict) else {},
            risk_mind_ref=risk_mind_ref,
            deps=risk_event_wiring,
        )

    def _apply_risk_learn_suggested(
        *,
        batch_idx: int,
        risk_obj: dict[str, Any],
        risk_mind_ref: str,
        risk_event_id: str,
    ) -> None:
        """Apply learn_suggested emitted by risk_judge."""

        handle_learn_suggested(
            learn_suggested=(risk_obj if isinstance(risk_obj, dict) else {}).get("learn_suggested"),
            batch_id=f"b{batch_idx}",
            source="risk_judge",
            mind_transcript_ref=risk_mind_ref,
            source_event_ids=[str(risk_event_id or "").strip()],
        )

    def _maybe_prompt_risk_continue(*, risk_obj: dict[str, Any]) -> bool | None:
        """Apply runtime violation policy; return False when user blocks run."""

        vr = runtime_cfg.get("violation_response") if isinstance(runtime_cfg.get("violation_response"), dict) else {}
        out = RP.maybe_prompt_risk_continue(
            risk_obj=risk_obj if isinstance(risk_obj, dict) else {},
            should_prompt_risk_user=AP.should_prompt_risk_user,
            violation_response_cfg=vr if isinstance(vr, dict) else {},
            read_user_answer=read_user_answer,
        )
        if out is False:
            set_status("blocked")
            set_notes("stopped after risk event")
            return False
        return out

    def judge_and_handle_risk(
        *,
        batch_idx: int,
        batch_id: str,
        risk_signals: list[str],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
    ) -> bool | None:
        """Run risk_judge, record evidence, and enforce runtime violation policy."""

        return RP.run_risk_predecide(
            batch_idx=batch_idx,
            batch_id=batch_id,
            risk_signals=risk_signals,
            hands_last=hands_last,
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=RP.RiskPredecideDeps(
                query_risk=_query_risk,
                record_risk=_record_risk_event,
                apply_learn_suggested=_apply_risk_learn_suggested,
                maybe_prompt_continue=_maybe_prompt_risk_continue,
            ),
        )

    return RiskPredecideWiringBundle(
        detect_risk_signals=detect_risk_signals,
        judge_and_handle_risk=judge_and_handle_risk,
    )
