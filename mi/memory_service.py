from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .memory_backends.base import MemoryBackend
from .memory_backends.in_memory import InMemoryBackend
from .memory_backends.sqlite_fts import SqliteFtsBackend
from .memory_ingest import ingest_learned_and_workflows, iter_project_ids
from .memory_snapshot import snapshot_item_from_event
from .memory_types import MemoryItem
from .paths import ProjectPaths
from .storage import iter_jsonl


class MemoryService:
    """A narrow interface around MI memory backends.

    Goal: keep callers (runner/cli) decoupled from the underlying backend so we can
    swap/extend memory implementations (e.g., vectors) later with minimal churn.
    """

    def __init__(self, home_dir: Path, *, backend: MemoryBackend | None = None, backend_name: str = "") -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._backend = backend or self._make_backend(backend_name)

    @property
    def home_dir(self) -> Path:
        return self._home_dir

    def _make_backend(self, backend_name: str) -> MemoryBackend:
        name = (backend_name or os.environ.get("MI_MEMORY_BACKEND") or "sqlite_fts").strip().lower()
        if name in ("sqlite_fts", "sqlite", "fts", "sqlitefts"):
            return SqliteFtsBackend(self._home_dir)
        if name in ("in_memory", "memory", "mem"):
            return InMemoryBackend()
        raise ValueError(f"unknown memory backend: {name}")

    def ingest_structured(self) -> None:
        """Sync small structured stores into the index (best-effort)."""
        ingest_learned_and_workflows(home_dir=self._home_dir, backend=self._backend)

    def upsert_items(self, items: list[MemoryItem]) -> None:
        self._backend.upsert_items(items)

    def search(
        self,
        *,
        query: str,
        top_k: int,
        kinds: set[str],
        include_global: bool,
        exclude_project_id: str,
    ) -> list[MemoryItem]:
        return self._backend.search(
            query=query,
            top_k=top_k,
            kinds=kinds,
            include_global=include_global,
            exclude_project_id=exclude_project_id,
        )

    def status(self) -> dict[str, Any]:
        return self._backend.status()

    def rebuild(self, *, include_snapshots: bool = True) -> dict[str, Any]:
        # Rebuild is best-effort and backend-dependent; it must never break MI runs.
        try:
            self._backend.reset()
        except Exception:
            pass

        self.ingest_structured()

        snap_count = 0
        if include_snapshots:
            batch: list[MemoryItem] = []
            for pid in iter_project_ids(self._home_dir):
                pp = ProjectPaths(home_dir=self._home_dir, project_root=Path("."), _project_id=str(pid))
                for obj in iter_jsonl(pp.evidence_log_path):
                    if not isinstance(obj, dict) or obj.get("kind") != "snapshot":
                        continue
                    it = snapshot_item_from_event(obj)
                    if not it:
                        continue
                    batch.append(it)
                    snap_count += 1
                    if len(batch) >= 200:
                        self._backend.upsert_items(batch)
                        batch = []
            if batch:
                self._backend.upsert_items(batch)

        st = self.status()
        st["rebuilt"] = True
        st["included_snapshots"] = bool(include_snapshots)
        st["indexed_snapshots"] = snap_count
        return st
