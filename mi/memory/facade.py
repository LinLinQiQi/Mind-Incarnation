from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .render import render_recall_context
from .snapshot import build_snapshot_item
from .types import MemoryItem
from .text import tokenize_query
from ..core.storage import now_rfc3339
from ..core.paths import ProjectPaths
from .service import MemoryService


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


@dataclass(frozen=True)
class CrossProjectRecallConfig:
    enabled: bool
    top_k: int
    max_chars: int
    include_kinds: set[str]
    exclude_current_project: bool
    prefer_current_project: bool
    triggers: dict[str, bool]

    @classmethod
    def from_runtime_config(cls, runtime_cfg: dict[str, Any]) -> CrossProjectRecallConfig:
        cfg = runtime_cfg.get("cross_project_recall") if isinstance(runtime_cfg.get("cross_project_recall"), dict) else {}
        enabled = bool(cfg.get("enabled", True))

        triggers = cfg.get("triggers") if isinstance(cfg.get("triggers"), dict) else {}
        tri = {
            "run_start": bool(triggers.get("run_start", True)),
            "before_ask_user": bool(triggers.get("before_ask_user", True)),
            "risk_signal": bool(triggers.get("risk_signal", True)),
        }

        try:
            top_k = int(cfg.get("top_k", 3) or 3)
        except Exception:
            top_k = 3
        top_k = max(1, min(10, top_k))

        try:
            max_chars = int(cfg.get("max_chars", 1800) or 1800)
        except Exception:
            max_chars = 1800
        max_chars = max(200, min(6000, max_chars))

        kinds_raw = cfg.get("include_kinds") if isinstance(cfg.get("include_kinds"), list) else ["snapshot", "workflow", "claim", "node"]
        include_kinds = {str(x).strip() for x in kinds_raw if str(x).strip()}
        # Back-compat: older configs may mention a "learned" kind. Ignore it.
        include_kinds.discard("learned")
        if not include_kinds:
            include_kinds = {"snapshot", "workflow", "claim"}

        exclude_current_project = bool(cfg.get("exclude_current_project", False))
        prefer_current_project = bool(cfg.get("prefer_current_project", True))

        return cls(
            enabled=enabled,
            top_k=top_k,
            max_chars=max_chars,
            include_kinds=include_kinds,
            exclude_current_project=exclude_current_project,
            prefer_current_project=prefer_current_project,
            triggers=tri,
        )

    def should_trigger(self, reason: str) -> bool:
        if not self.enabled:
            return False
        r = str(reason or "").strip()
        if not r:
            return False
        return bool(self.triggers.get(r, False))


@dataclass(frozen=True)
class RecallOutcome:
    evidence_event: dict[str, Any]
    window_entry: dict[str, Any]


@dataclass(frozen=True)
class SnapshotOutcome:
    evidence_event: dict[str, Any]
    window_entry: dict[str, Any]
    indexed_item: MemoryItem


class MemoryFacade:
    """A small facade around MI's memory/recall system (V1 text index).

    Runner should not depend on the underlying index implementation; keep all
    "materialized view" mechanics here so memory backends can evolve.
    """

    def __init__(self, *, home_dir: Path, project_paths: ProjectPaths, runtime_cfg: dict[str, Any]) -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._project_paths = project_paths
        self._recall_cfg = CrossProjectRecallConfig.from_runtime_config(runtime_cfg if isinstance(runtime_cfg, dict) else {})
        self._mem = MemoryService(self._home_dir)
        self._last_recall_key = ""

    def maybe_cross_project_recall(self, *, batch_id: str, reason: str, query: str, thread_id: str) -> RecallOutcome | None:
        if not self._recall_cfg.should_trigger(reason):
            return None

        q_raw = str(query or "").strip()
        if not q_raw:
            return None

        toks = tokenize_query(q_raw, max_tokens=24)
        q_compact = " ".join(toks).strip()
        if not q_compact:
            return None

        # Guard: avoid repeated identical recalls in a tight loop.
        key = f"{reason}:{_truncate(q_compact, 160)}"
        if key == self._last_recall_key:
            return None
        self._last_recall_key = key

        # Ingest small structured stores (workflows/claims) before querying.
        self._mem.ingest_structured()

        exclude_pid = self._project_paths.project_id if self._recall_cfg.exclude_current_project else ""
        # Fetch more candidates and re-rank to prefer current project/global items.
        candidate_k = min(50, max(self._recall_cfg.top_k, self._recall_cfg.top_k * 5))
        items = self._mem.search(
            query=q_compact,
            top_k=candidate_k,
            kinds=set(self._recall_cfg.include_kinds),
            include_global=True,
            exclude_project_id=exclude_pid,
        )
        if not items:
            return None

        # Prefer current project (when allowed) -> global -> other projects.
        cur_pid = str(self._project_paths.project_id or "").strip()
        if self._recall_cfg.prefer_current_project and cur_pid and not exclude_pid:
            def tier(it: MemoryItem) -> int:
                if it.scope == "project" and it.project_id == cur_pid:
                    return 0
                if it.scope == "global":
                    return 1
                return 2
        else:
            def tier(it: MemoryItem) -> int:
                return 0 if it.scope == "global" else 1

        items2 = sorted(items, key=tier)  # stable sort preserves within-tier rank
        items = items2[: self._recall_cfg.top_k]

        rendered_items, context_text = render_recall_context(items=items, max_chars=self._recall_cfg.max_chars)
        ev = {
            "kind": "cross_project_recall",
            "batch_id": batch_id,
            "ts": now_rfc3339(),
            "thread_id": (thread_id or "").strip(),
            "reason": reason,
            # `query` is the effective compact query used for search.
            "query": _truncate(q_compact, 800),
            "query_raw": _truncate(q_raw, 800),
            "query_compact": _truncate(q_compact, 800),
            "tokens_used": toks,
            "top_k": self._recall_cfg.top_k,
            "include_kinds": sorted(self._recall_cfg.include_kinds),
            "exclude_current_project": bool(self._recall_cfg.exclude_current_project),
            "prefer_current_project": bool(self._recall_cfg.prefer_current_project),
            "items": rendered_items,
            "context_text": context_text,
        }
        win = {
            "kind": "cross_project_recall",
            "batch_id": batch_id,
            "reason": reason,
            "query": _truncate(q_compact, 200),
            "query_raw": _truncate(q_raw, 200),
            "query_compact": _truncate(q_compact, 200),
            "tokens_used": toks,
            "items": rendered_items,
        }
        return RecallOutcome(evidence_event=ev, window_entry=win)

    def upsert_items(self, items: list[MemoryItem]) -> None:
        """Upsert items into the memory index (best-effort; must not raise)."""
        try:
            self._mem.upsert_items(items)
        except Exception:
            return

    def materialize_snapshot(
        self,
        *,
        segment_state: dict[str, Any],
        segment_records: list[dict[str, Any]],
        batch_id: str,
        thread_id: str,
        task_fallback: str,
        checkpoint_kind: str,
        status_hint: str,
        checkpoint_notes: str,
    ) -> SnapshotOutcome | None:
        """Build+index a snapshot based on the current segment buffer."""

        try:
            seg_id = str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else ""
            task_hint = str(segment_state.get("task_hint") or "") if isinstance(segment_state, dict) else ""
            if not task_hint:
                task_hint = str(task_fallback or "")
            snap_ev, snap_item = build_snapshot_item(
                project_id=self._project_paths.project_id,
                segment_id=seg_id,
                thread_id=(thread_id or "").strip(),
                batch_id=batch_id,
                task_hint=task_hint,
                checkpoint_kind=str(checkpoint_kind or ""),
                status_hint=str(status_hint or ""),
                checkpoint_notes=str(checkpoint_notes or ""),
                segment_records=segment_records,
            )
            self._mem.upsert_items([snap_item])
        except Exception:
            return None

        win = {
            "kind": "snapshot",
            "batch_id": snap_ev.get("batch_id"),
            "checkpoint_kind": snap_ev.get("checkpoint_kind"),
            "status_hint": snap_ev.get("status_hint"),
            "tags": snap_ev.get("tags"),
            "text": _truncate(str(snap_ev.get("text") or ""), 600),
        }
        return SnapshotOutcome(evidence_event=snap_ev, window_entry=win, indexed_item=snap_item)
