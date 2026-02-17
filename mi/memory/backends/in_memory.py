from __future__ import annotations

from typing import Any

from ..text import tokenize_query
from ..types import MemoryGroup, MemoryItem


def _lower_haystack(it: MemoryItem) -> str:
    return " ".join([it.title or "", it.body or "", " ".join(it.tags or [])]).lower()


class InMemoryBackend:
    """A tiny in-process memory backend (primarily for tests)."""

    name = "in_memory"

    def __init__(self, home_dir: object | None = None) -> None:
        self._items: dict[str, MemoryItem] = {}

    def reset(self) -> None:
        self._items.clear()

    def upsert_items(self, items: list[MemoryItem]) -> None:
        for it in items or []:
            if not isinstance(it, MemoryItem):
                continue
            if not str(it.item_id or "").strip():
                continue
            self._items[it.item_id] = it

    def sync_groups(self, groups: list[MemoryGroup], *, existing_project_ids: set[str] | None = None) -> None:
        # Upsert all group items.
        keep_by_group: dict[tuple[str, str, str], set[str]] = {}
        for g in groups or []:
            key = (str(g.kind or ""), str(g.scope or ""), str(g.project_id or ""))
            keep_ids: set[str] = set()
            for it in g.items or []:
                if not isinstance(it, MemoryItem):
                    continue
                if it.item_id:
                    keep_ids.add(it.item_id)
                self._items[it.item_id] = it
            keep_by_group[key] = keep_ids

        # Prune stale items from groups we were asked to sync.
        for key, keep_ids in keep_by_group.items():
            kind, scope, pid = key
            stale: list[str] = []
            for it in self._items.values():
                if it.kind == kind and it.scope == scope and it.project_id == pid:
                    if it.item_id not in keep_ids:
                        stale.append(it.item_id)
            for item_id in stale:
                self._items.pop(item_id, None)

        # Prune orphaned project-scoped learned/workflow items when projects were deleted.
        if existing_project_ids is not None:
            keep = {str(x).strip() for x in existing_project_ids if str(x).strip()}
            stale2: list[str] = []
            for it in self._items.values():
                if it.scope != "project":
                    continue
                if it.kind not in ("learned", "workflow"):
                    continue
                if it.project_id and it.project_id not in keep:
                    stale2.append(it.item_id)
            for item_id in stale2:
                self._items.pop(item_id, None)

    def search(
        self,
        *,
        query: str,
        top_k: int,
        kinds: set[str],
        include_global: bool,
        exclude_project_id: str,
    ) -> list[MemoryItem]:
        toks = tokenize_query(query)
        if not toks or top_k <= 0:
            return []
        kind_allow = {str(k).strip() for k in (kinds or set()) if str(k).strip()}
        out: list[tuple[int, MemoryItem]] = []
        for it in self._items.values():
            if kind_allow and it.kind not in kind_allow:
                continue
            if exclude_project_id and it.scope == "project" and it.project_id == exclude_project_id:
                continue
            if not include_global and it.scope == "global":
                continue

            hay = _lower_haystack(it)
            score = 0
            for t in toks:
                if t in hay:
                    score += 1
            if score <= 0:
                continue
            out.append((score, it))

        # Sort: higher score first; stable fallback by ts desc.
        out.sort(key=lambda x: (x[0], str(x[1].ts or "")), reverse=True)
        return [it for _, it in out[:top_k]]

    def status(self) -> dict[str, Any]:
        groups: dict[tuple[str, str, str], int] = {}
        for it in self._items.values():
            k = (it.kind, it.scope, it.project_id)
            groups[k] = groups.get(k, 0) + 1
        return {
            "backend": self.name,
            "exists": True,
            "total_items": len(self._items),
            "groups": [{"kind": k[0], "scope": k[1], "project_id": k[2], "count": n} for k, n in sorted(groups.items())],
        }
