from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .backends.base import MemoryBackend
from .backends.in_memory import InMemoryBackend
from .backends.sqlite_fts import SqliteFtsBackend
from .ingest import ingest_learned_and_workflows, iter_project_ids, _active_node_items_for_paths
from .snapshot import snapshot_item_from_event
from .types import MemoryItem
from ..core.paths import GlobalPaths, ProjectPaths
from ..core.storage import iter_jsonl


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
        node_count = 0
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

        # Thought DB nodes are indexed during rebuild (best-effort). This is a backfill path:
        # incremental node indexing happens when MI materializes nodes during `mi run`.
        try:
            gp = GlobalPaths(home_dir=self._home_dir)
            global_nodes = _active_node_items_for_paths(
                nodes_path=gp.thoughtdb_global_nodes_path,
                edges_path=gp.thoughtdb_global_edges_path,
                scope="global",
                project_id="",
            )
            node_count += len(global_nodes)
            for i in range(0, len(global_nodes), 200):
                self._backend.upsert_items(global_nodes[i : i + 200])
        except Exception:
            pass

        try:
            for pid in iter_project_ids(self._home_dir):
                pp = ProjectPaths(home_dir=self._home_dir, project_root=Path("."), _project_id=str(pid))
                items = _active_node_items_for_paths(
                    nodes_path=pp.thoughtdb_nodes_path,
                    edges_path=pp.thoughtdb_edges_path,
                    scope="project",
                    project_id=str(pid),
                )
                node_count += len(items)
                for i in range(0, len(items), 200):
                    self._backend.upsert_items(items[i : i + 200])
        except Exception:
            pass

        st = self.status()
        st["rebuilt"] = True
        st["included_snapshots"] = bool(include_snapshots)
        st["indexed_snapshots"] = snap_count
        st["indexed_nodes"] = node_count
        return st
