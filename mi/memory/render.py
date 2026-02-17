from __future__ import annotations

from typing import Any

from .text import truncate
from .types import MemoryItem


def render_recall_context(*, items: list[MemoryItem], max_chars: int) -> tuple[list[dict[str, Any]], str]:
    """Render a compact recall context (for EvidenceLog + prompt injection)."""

    rendered: list[dict[str, Any]] = []
    lines: list[str] = []
    lines.append("[Cross-Project Recall]")
    if not items:
        lines.append("(none)")
        return rendered, "\n".join(lines).strip() + "\n"

    budget = max(200, int(max_chars))
    used = 0
    for it in items:
        proj = it.project_id if it.scope == "project" else "global"
        head = f"- ({it.kind}/{it.scope} from {proj} @ {it.ts}) {it.title}".strip()
        snippet = truncate(it.body.strip().replace("\n", " "), 320)
        block = head + ("\n  " + snippet if snippet else "")
        if used + len(block) > budget and rendered:
            break
        used += len(block) + 1
        lines.append(block)
        rendered.append(
            {
                "item_id": it.item_id,
                "kind": it.kind,
                "scope": it.scope,
                "project_id": it.project_id,
                "ts": it.ts,
                "title": it.title,
                "snippet": snippet,
                "tags": it.tags,
                "source_refs": it.source_refs,
            }
        )
    return rendered, "\n".join(lines).strip() + "\n"
