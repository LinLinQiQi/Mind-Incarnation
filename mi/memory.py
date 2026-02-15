from __future__ import annotations

"""Memory public surface (V1).

MI's memory system is a *materialized view* for recall. Sources of truth live in:
- learned.jsonl (global + per-project)
- workflows/*.json (global + per-project)
- EvidenceLog snapshot records (per-project)

The implementation is intentionally layered so we can swap backends later without
rewiring runner/CLI code.
"""

from .memory_backends.sqlite_fts import SqliteFtsBackend as MemoryIndex  # back-compat name
from .memory_ingest import ingest_learned_and_workflows, iter_project_ids
from .memory_render import render_recall_context
from .memory_snapshot import build_snapshot_item, snapshot_item_from_event
from .memory_types import MemoryGroup, MemoryItem

__all__ = [
    # Types
    "MemoryItem",
    "MemoryGroup",
    # Ingestion helpers
    "iter_project_ids",
    "ingest_learned_and_workflows",
    # Snapshot helpers
    "build_snapshot_item",
    "snapshot_item_from_event",
    # Rendering
    "render_recall_context",
    # Back-compat index name (sqlite backend)
    "MemoryIndex",
]

