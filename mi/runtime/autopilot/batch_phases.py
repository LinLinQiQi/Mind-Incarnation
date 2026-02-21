from __future__ import annotations

from typing import Any


def extract_evidence_counts(evidence_obj: dict[str, Any] | None) -> dict[str, int]:
    """Return compact counters for evidence extraction logging."""

    obj = evidence_obj if isinstance(evidence_obj, dict) else {}
    return {
        "facts": len(obj.get("facts") or []) if isinstance(obj.get("facts"), list) else 0,
        "actions": len(obj.get("actions") or []) if isinstance(obj.get("actions"), list) else 0,
        "results": len(obj.get("results") or []) if isinstance(obj.get("results"), list) else 0,
        "unknowns": len(obj.get("unknowns") or []) if isinstance(obj.get("unknowns"), list) else 0,
        "risk_signals": len(obj.get("risk_signals") or []) if isinstance(obj.get("risk_signals"), list) else 0,
    }


def build_risk_fallback(risk_signals: list[str], *, state: str) -> dict[str, Any]:
    """Conservative fallback used when risk_judge cannot return an object."""

    category = "other"
    severity = "high"
    for s in risk_signals:
        prefix = s.split(":", 1)[0].strip().lower()
        if prefix in ("network", "install", "push", "publish", "delete", "privilege"):
            category = prefix
            break
    if category == "delete":
        severity = "critical"
    msg = "mind_circuit_open: risk_judge skipped; treat as high risk" if state == "skipped" else "mind_error: risk_judge failed; treat as high risk"
    return {
        "category": category,
        "severity": severity,
        "should_ask_user": True,
        "mitigation": [msg],
        "learn_suggested": [],
    }


def should_prompt_risk_user(*, risk_obj: dict[str, Any], violation_response_cfg: dict[str, Any]) -> bool:
    """Apply runtime risk escalation policy to one risk_judge output."""

    ask_user = bool(violation_response_cfg.get("ask_user_on_high_risk", True))
    severity = str(risk_obj.get("severity") or "low")
    should_ask_user = bool(risk_obj.get("should_ask_user", False))
    category = str(risk_obj.get("category") or "other")

    sev_list = violation_response_cfg.get("ask_user_risk_severities")
    if isinstance(sev_list, list) and any(str(x).strip() for x in sev_list):
        sev_allow = {str(x).strip() for x in sev_list if str(x).strip()}
    else:
        sev_allow = {"high", "critical"}

    cat_list = violation_response_cfg.get("ask_user_risk_categories")
    if isinstance(cat_list, list) and any(str(x).strip() for x in cat_list):
        cat_allow = {str(x).strip() for x in cat_list if str(x).strip()}
    else:
        cat_allow = set()

    respect_should = bool(violation_response_cfg.get("ask_user_respect_should_ask_user", True))

    return ask_user and (severity in sev_allow) and (not cat_allow or category in cat_allow) and (should_ask_user if respect_should else True)
