from __future__ import annotations

from pathlib import Path
from typing import Any

from .memory import MemoryIndex, MemoryItem, ingest_learned_and_workflows, rebuild_memory_index


class MemoryService:
    """A narrow interface around MI memory backends (V1: sqlite text index).

    Goal: keep callers (runner/cli) decoupled from the underlying backend so we can
    swap/extend memory implementations (e.g., vectors) later with minimal churn.
    """

    def __init__(self, home_dir: Path) -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._index = MemoryIndex(self._home_dir)

    @property
    def home_dir(self) -> Path:
        return self._home_dir

    def ingest_structured(self) -> None:
        """Sync small structured stores into the index (best-effort)."""
        ingest_learned_and_workflows(home_dir=self._home_dir, index=self._index)

    def upsert_items(self, items: list[MemoryItem]) -> None:
        self._index.upsert_items(items)

    def search(
        self,
        *,
        query: str,
        top_k: int,
        kinds: set[str],
        include_global: bool,
        exclude_project_id: str,
    ) -> list[MemoryItem]:
        return self._index.search(
            query=query,
            top_k=top_k,
            kinds=kinds,
            include_global=include_global,
            exclude_project_id=exclude_project_id,
        )

    def status(self) -> dict[str, Any]:
        return self._index.status()

    def rebuild(self, *, include_snapshots: bool = True) -> dict[str, Any]:
        # Rebuild is implemented as a helper that deletes and recreates the index.
        return rebuild_memory_index(home_dir=self._home_dir, include_snapshots=include_snapshots)

