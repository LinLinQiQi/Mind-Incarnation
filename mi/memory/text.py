from __future__ import annotations

import re


def truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def tokenize_query(text: str, *, max_tokens: int = 24) -> list[str]:
    """Tokenize a user query into safe FTS-ish tokens.

    We intentionally avoid characters like ':' and leading '-' that can be treated
    as operators in sqlite FTS query syntax (risk signals often contain "push:" / "-rf").
    """

    toks = re.findall(r"[A-Za-z0-9_]{2,}", (text or "").lower())
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_tokens:
            break
    return out

