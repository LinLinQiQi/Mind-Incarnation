from __future__ import annotations

from typing import Any


def _looks_like_user_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lower = t.lower()
    if "?" in t:
        return True
    patterns = [
        "do you want",
        "would you like",
        "should i",
        "shall i",
        "can i",
        "may i",
        "please confirm",
        "please provide",
        "which option",
        "choose one",
        "pick one",
        "need your",
        "need you to",
        "before i proceed",
        "to continue",
        "what should",
        "any preference",
    ]
    return any(p in lower for p in patterns)


def _empty_auto_answer() -> dict[str, Any]:
    return {
        "should_answer": False,
        "confidence": 0.0,
        "hands_answer_input": "",
        "needs_user_input": False,
        "ask_user_question": "",
        "unanswered_questions": [],
        "notes": "",
    }


def _empty_evidence_obj(*, note: str = "") -> dict[str, Any]:
    unknowns: list[str] = []
    if note.strip():
        unknowns.append(note.strip())
    return {
        "facts": [],
        "actions": [],
        "results": [],
        "unknowns": unknowns,
        "risk_signals": [],
    }


def _empty_check_plan() -> dict[str, Any]:
    return {
        "should_run_checks": False,
        "needs_testless_strategy": False,
        "testless_strategy_question": "",
        "check_goals": [],
        "commands_hints": [],
        "hands_check_input": "",
        "notes": "",
    }


def _should_plan_checks(
    *,
    summary: dict[str, Any],
    evidence_obj: dict[str, Any],
    hands_last_message: str,
    repo_observation: dict[str, Any],
) -> bool:
    try:
        if int(summary.get("exit_code") or 0) != 0:
            return True
    except Exception:
        return True

    unknowns = evidence_obj.get("unknowns") if isinstance(evidence_obj, dict) else None
    if isinstance(unknowns, list) and any(str(x).strip() for x in unknowns):
        return True

    rs = evidence_obj.get("risk_signals") if isinstance(evidence_obj, dict) else None
    if isinstance(rs, list) and any(str(x).strip() for x in rs):
        return True

    if _looks_like_user_question(hands_last_message):
        return True

    if isinstance(repo_observation, dict):
        for k in ("git_status_porcelain", "git_diff_stat", "git_diff_cached_stat"):
            v = repo_observation.get(k)
            if isinstance(v, str) and v.strip():
                return True

    return False
