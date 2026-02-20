from __future__ import annotations

import hashlib


def _normalize_for_sig(text: str, limit: int) -> str:
    t = " ".join((text or "").strip().split()).lower()
    return t[:limit]


def _loop_sig(*, hands_last_message: str, next_input: str) -> str:
    data = _normalize_for_sig(hands_last_message, 2000) + "\n---\n" + _normalize_for_sig(next_input, 2000)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _loop_pattern(sigs: list[str]) -> str:
    if len(sigs) >= 3 and sigs[-1] == sigs[-2] == sigs[-3]:
        return "aaa"
    if len(sigs) >= 4 and sigs[-1] == sigs[-3] and sigs[-2] == sigs[-4]:
        return "abab"
    return ""
