from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryItem:
    """A recallable memory unit (materialized view) with traceable sources."""

    item_id: str
    kind: str  # snapshot|learned|workflow
    scope: str  # global|project
    project_id: str  # empty for global scope
    ts: str
    title: str
    body: str
    tags: list[str]
    source_refs: list[dict[str, Any]]


@dataclass(frozen=True)
class MemoryGroup:
    """A group of items to sync+prune as a unit (kind+scope+project_id)."""

    kind: str
    scope: str
    project_id: str
    items: list[MemoryItem]

