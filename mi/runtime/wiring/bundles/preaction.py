from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mi.runtime import autopilot as AP
from mi.runtime import prompts as P
import mi.runtime.wiring as W


@dataclass(frozen=True)
class PreactionWiringBundle:
    """Runner wiring bundle for pre-action arbitration (behavior-preserving)."""

    apply_preactions: Callable[..., tuple[bool | None, dict[str, Any]]]


def build_preaction_wiring_bundle(
    *,
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    overlay: dict[str, Any],
    evidence_window: list[dict[str, Any]],
    maybe_cross_project_recall: Callable[..., None],
    mind_call: Callable[..., tuple[Any, str, str]],
    append_auto_answer_record: Callable[..., dict[str, Any]],
    get_check_input: Callable[[dict[str, Any] | None], str],
    join_hands_inputs: Callable[[str, str], str],
    queue_next_input: Callable[..., bool],
    read_user_answer: Callable[[str], str],
    append_user_input_record: Callable[..., dict[str, Any]],
    set_blocked: Callable[[str], None],
    resolve_tls_for_checks: Callable[..., tuple[dict[str, Any], str]],
) -> PreactionWiringBundle:
    """Build wiring for deterministic pre-action arbitration before decide_next."""

    predecide_user_deps = W.PredecideUserWiringDeps(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        recent_evidence=evidence_window,
        empty_auto_answer=AP._empty_auto_answer,
        maybe_cross_project_recall=maybe_cross_project_recall,
        auto_answer_prompt_builder=P.auto_answer_to_hands_prompt,
        mind_call=mind_call,
        append_auto_answer_record=append_auto_answer_record,
        get_check_input=get_check_input,
        join_hands_inputs=join_hands_inputs,
        queue_next_input=queue_next_input,
        read_user_answer=read_user_answer,
        append_user_input_record=append_user_input_record,
        set_blocked=set_blocked,
    )

    def apply_preactions(
        *,
        batch_idx: int,
        hands_last: str,
        repo_obs: dict[str, Any],
        tdb_ctx_batch_obj: dict[str, Any],
        checks_obj: dict[str, Any],
        auto_answer_obj: dict[str, Any],
    ) -> tuple[bool | None, dict[str, Any]]:
        """Apply deterministic pre-action arbitration before decide_next."""

        if isinstance(auto_answer_obj, dict) and bool(auto_answer_obj.get("needs_user_input", False)):
            handled, checks_out = W.handle_auto_answer_needs_user_wired(
                batch_idx=batch_idx,
                hands_last=hands_last,
                repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
                tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
                checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
                auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
                deps=predecide_user_deps,
            )
            return bool(handled), checks_out if isinstance(checks_out, dict) else {}

        checks_obj2, block_reason = resolve_tls_for_checks(
            checks_obj=checks_obj if isinstance(checks_obj, dict) else AP._empty_check_plan(),
            hands_last_message=hands_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            user_input_batch_id=f"b{batch_idx}",
            batch_id_after_testless=f"b{batch_idx}.after_testless",
            batch_id_after_tls_claim=f"b{batch_idx}.after_tls_claim",
            tag_after_testless=f"checks_after_tls_b{batch_idx}",
            tag_after_tls_claim=f"checks_after_tls_claim_b{batch_idx}",
            notes_prefix="",
            source="user_input:testless_strategy",
            rationale="user provided testless verification strategy",
        )
        if block_reason:
            set_blocked(str(block_reason or ""))
            return False, checks_obj2 if isinstance(checks_obj2, dict) else AP._empty_check_plan()

        answer_text = ""
        if isinstance(auto_answer_obj, dict) and bool(auto_answer_obj.get("should_answer", False)):
            answer_text = str(auto_answer_obj.get("hands_answer_input") or "").strip()
        queued = W.try_queue_answer_with_checks_wired(
            batch_id=f"b{batch_idx}",
            queue_reason="sent auto-answer/checks to Hands",
            answer_text=answer_text,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj2 if isinstance(checks_obj2, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=predecide_user_deps,
        )
        if isinstance(queued, bool):
            return bool(queued), checks_obj2 if isinstance(checks_obj2, dict) else AP._empty_check_plan()
        return None, checks_obj2 if isinstance(checks_obj2, dict) else AP._empty_check_plan()

    return PreactionWiringBundle(apply_preactions=apply_preactions)
