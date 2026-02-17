from __future__ import annotations

import re


_REPLACEMENT = "[REDACTED]"


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Generic "key=value" / "key: value" patterns.
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*([^\s'\"\\]+)"), r"\1=" + _REPLACEMENT),
    # Authorization headers.
    (re.compile(r"(?i)\b(authorization)\s*:\s*(bearer|basic)\s+([^\s]+)"), r"\1: \2 " + _REPLACEMENT),
    # OpenAI-style keys (best-effort).
    (re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_\-]{10,})\b"), _REPLACEMENT),
    # GitHub tokens.
    (re.compile(r"\b(ghp_[A-Za-z0-9]{20,})\b"), _REPLACEMENT),
    (re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"), _REPLACEMENT),
    # AWS access key ids (do not redact everything that looks like AKIA unless it matches).
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), _REPLACEMENT),
]


def redact_text(text: str) -> str:
    """Redact common secret/token patterns from text for safe display.

    This is best-effort and intended for CLI display only (stored logs remain unchanged).
    """

    out = text or ""
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out

