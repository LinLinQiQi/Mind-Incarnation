from __future__ import annotations

from typing import Iterable


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _dedup_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


# Central marker lists used by both risk-signal extraction and the optional interrupt/terminate mode.
_MARKERS_ANY_EXTERNAL = [
    # Package install / dependency changes.
    "pip install",
    "npm install",
    "pnpm install",
    "yarn add",
    # Network fetches.
    "curl ",
    "wget ",
    # Publishing / pushing / irreversible external actions.
    "git push",
    "npm publish",
    "twine upload",
    # Local destructive / privilege escalation.
    "rm -rf",
    "rm -r",
    "sudo ",
]

_MARKERS_HIGH_RISK = [
    # External irreversible actions.
    "git push",
    "npm publish",
    "twine upload",
    # Destructive / privilege escalation.
    "rm -rf",
    "rm -r",
    "sudo ",
    # "Pipe to shell" patterns (often high risk).
    "curl | sh",
    "curl|sh",
    "wget | sh",
    "wget|sh",
]


def should_interrupt_text(mode: str, text: str) -> bool:
    """Best-effort predicate for interrupt/terminate mode.

    mode: off|on_high_risk|on_any_external
    """

    mode = (mode or "").strip()
    if mode == "off":
        return False
    lower = (text or "").lower()
    markers = _MARKERS_ANY_EXTERNAL if mode == "on_any_external" else _MARKERS_HIGH_RISK
    return any(m in lower for m in markers)


def detect_risk_signals_from_command(cmd: str) -> list[str]:
    """Return risk signals in the format: '<category>: <detail>'."""

    cmd = str(cmd or "")
    lower = cmd.lower()
    signals: list[str] = []

    if "git push" in lower:
        signals.append(f"push: {cmd}")
    if "npm publish" in lower or "twine upload" in lower:
        signals.append(f"publish: {cmd}")
    if "pip install" in lower or "npm install" in lower or "pnpm install" in lower or "yarn add" in lower:
        signals.append(f"install: {cmd}")
    if "curl " in lower or "wget " in lower:
        signals.append(f"network: {cmd}")
    if "rm -rf" in lower or "rm -r" in lower:
        signals.append(f"delete: {cmd}")
    if "sudo " in lower:
        signals.append(f"privilege: {cmd}")

    return _dedup_keep_order(signals)


def detect_risk_signals_from_text_line(line: str, *, limit: int = 200) -> list[str]:
    """Best-effort risk detection from raw stdout/stderr transcript text."""

    raw = str(line or "").strip()
    if not raw:
        return []
    lower = raw.lower()
    detail = _truncate(raw, limit)

    signals: list[str] = []
    if "git push" in lower:
        signals.append(f"push: {detail}")
    if "npm publish" in lower or "twine upload" in lower:
        signals.append(f"publish: {detail}")
    if "pip install" in lower or "npm install" in lower or "pnpm install" in lower or "yarn add" in lower:
        signals.append(f"install: {detail}")
    if "curl " in lower or "wget " in lower:
        signals.append(f"network: {detail}")
    if "rm -rf" in lower or "rm -r" in lower:
        signals.append(f"delete: {detail}")
    if "sudo " in lower:
        signals.append(f"privilege: {detail}")

    return _dedup_keep_order(signals)

