from __future__ import annotations

from typing import Any, Protocol

from ..memory_types import MemoryGroup, MemoryItem


class MemoryBackend(Protocol):
    """Backend interface for MI memory (text index in V1).

    Backends are a materialized view only: sources of truth live in MI stores.
    """

    name: str

    def reset(self) -> None: ...

    def upsert_items(self, items: list[MemoryItem]) -> None: ...

    def sync_groups(self, groups: list[MemoryGroup], *, existing_project_ids: set[str] | None = None) -> None: ...

    def search(
        self,
        *,
        query: str,
        top_k: int,
        kinds: set[str],
        include_global: bool,
        exclude_project_id: str,
    ) -> list[MemoryItem]: ...

    def status(self) -> dict[str, Any]: ...

