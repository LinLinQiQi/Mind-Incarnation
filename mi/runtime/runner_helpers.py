from __future__ import annotations

from typing import Any


def dict_or_empty(obj: Any) -> dict[str, Any]:
    """Return obj if it's a dict; otherwise return an empty dict.

    Used to normalize best-effort JSON outputs from provider adapters.
    """

    return obj if isinstance(obj, dict) else {}


def get_check_input(checks_obj: dict[str, Any] | None) -> str:
    """Return hands_check_input when should_run_checks=true (best-effort)."""

    if not isinstance(checks_obj, dict):
        return ""
    if not bool(checks_obj.get("should_run_checks", False)):
        return ""
    return str(checks_obj.get("hands_check_input") or "").strip()

