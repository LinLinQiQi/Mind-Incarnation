from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.paths import GlobalPaths, ProjectPaths
from ..core.storage import ensure_dir, iter_jsonl
from .append_store import ThoughtAppendStore
from .model import (
    ThoughtDbView,
    claim_signature,
    new_claim_id,
    new_edge_id,
    new_node_id,
)
from .service_store import ThoughtServiceStore
from .view_store import ThoughtViewStore


class ThoughtDbStore:
    """Append-only Thought DB store facade.

    Source of truth for MI runs remains EvidenceLog + raw transcripts. Thought DB
    adds durable, reusable Claim/Edge/Node records that reference EvidenceLog event_id.

    Internal layering:
    - ThoughtAppendStore: append-only writes
    - ThoughtViewStore: materialized view + snapshot/cache
    - ThoughtServiceStore: mined-output application and business rules
    """

    def __init__(self, *, home_dir: Path, project_paths: ProjectPaths) -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._project_paths = project_paths
        self._gp = GlobalPaths(home_dir=self._home_dir)

        self._view = ThoughtViewStore(
            claims_path_for_scope=self._claims_path,
            edges_path_for_scope=self._edges_path,
            nodes_path_for_scope=self._nodes_path,
            iter_jsonl_reader=self._iter_jsonl_reader,
            project_id_for_scope=self._project_id_for_scope,
            scope_metas=self._scope_metas,
            view_snapshot_path=self._view_snapshot_path,
        )
        self._append = ThoughtAppendStore(
            claims_path_for_scope=self._claims_path,
            edges_path_for_scope=self._edges_path,
            nodes_path_for_scope=self._nodes_path,
            project_id_for_scope=self._project_id_for_scope,
            ensure_scope_dirs=self._ensure_scope_dirs,
            on_append=lambda scope, obj: self._view.update_cache_after_append(scope=scope, obj=obj),
        )
        self._service = ThoughtServiceStore(
            append_store=self._append,
            view_store=self._view,
            project_id_for_scope=self._project_id_for_scope,
        )

    @property
    def home_dir(self) -> Path:
        return self._home_dir

    def _scope_metas(self, scope: str) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
        def meta(p: Path) -> tuple[int, int]:
            try:
                st = p.stat()
            except FileNotFoundError:
                return 0, 0
            except Exception:
                return 0, 0
            return int(getattr(st, "st_size", 0) or 0), int(getattr(st, "st_mtime_ns", 0) or 0)

        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        return meta(self._claims_path(sc)), meta(self._edges_path(sc)), meta(self._nodes_path(sc))

    def _claims_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_claims_path
        return self._project_paths.thoughtdb_claims_path

    def _edges_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_edges_path
        return self._project_paths.thoughtdb_edges_path

    def _nodes_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_nodes_path
        return self._project_paths.thoughtdb_nodes_path

    def _project_id_for_scope(self, scope: str) -> str:
        return "" if scope == "global" else self._project_paths.project_id

    def _ensure_scope_dirs(self, scope: str) -> None:
        claims = self._claims_path(scope)
        edges = self._edges_path(scope)
        nodes = self._nodes_path(scope)
        ensure_dir(claims.parent)
        ensure_dir(edges.parent)
        ensure_dir(nodes.parent)

    def _iter_jsonl_reader(self, path: Path):
        # Keep this indirection so tests can patch `mi.thoughtdb.store.iter_jsonl`.
        return iter_jsonl(path)

    def _view_snapshot_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_dir / "view.snapshot.json"
        return self._project_paths.thoughtdb_dir / "view.snapshot.json"

    # View layer
    def flush_snapshots_best_effort(self) -> None:
        self._view.flush_snapshots_best_effort()

    def load_view(self, *, scope: str) -> ThoughtDbView:
        return self._view.load_view(scope=scope)

    def existing_signatures(self, *, scope: str) -> set[str]:
        return self._view.existing_signatures(scope=scope)

    def existing_signature_map(self, *, scope: str) -> dict[str, str]:
        return self._view.existing_signature_map(scope=scope)

    def existing_edge_keys(self, *, scope: str) -> set[str]:
        return self._view.existing_edge_keys(scope=scope)

    # Append layer
    def append_claim_create(
        self,
        *,
        claim_type: str,
        text: str,
        scope: str,
        visibility: str,
        valid_from: str | None,
        valid_to: str | None,
        tags: list[str],
        source_event_ids: list[str],
        confidence: float,
        notes: str,
    ) -> str:
        return self._append.append_claim_create(
            claim_type=claim_type,
            text=text,
            scope=scope,
            visibility=visibility,
            valid_from=valid_from,
            valid_to=valid_to,
            tags=tags,
            source_event_ids=source_event_ids,
            confidence=confidence,
            notes=notes,
        )

    def append_claim_retract(
        self,
        *,
        claim_id: str,
        scope: str,
        rationale: str,
        source_event_ids: list[str],
    ) -> None:
        self._append.append_claim_retract(
            claim_id=claim_id,
            scope=scope,
            rationale=rationale,
            source_event_ids=source_event_ids,
        )

    def append_node_create(
        self,
        *,
        node_type: str,
        title: str,
        text: str,
        scope: str,
        visibility: str,
        tags: list[str],
        source_event_ids: list[str],
        confidence: float,
        notes: str,
    ) -> str:
        return self._append.append_node_create(
            node_type=node_type,
            title=title,
            text=text,
            scope=scope,
            visibility=visibility,
            tags=tags,
            source_event_ids=source_event_ids,
            confidence=confidence,
            notes=notes,
        )

    def append_node_retract(
        self,
        *,
        node_id: str,
        scope: str,
        rationale: str,
        source_event_ids: list[str],
    ) -> None:
        self._append.append_node_retract(
            node_id=node_id,
            scope=scope,
            rationale=rationale,
            source_event_ids=source_event_ids,
        )

    def append_edge(
        self,
        *,
        edge_type: str,
        from_id: str,
        to_id: str,
        scope: str,
        visibility: str,
        source_event_ids: list[str],
        notes: str,
    ) -> str:
        return self._append.append_edge(
            edge_type=edge_type,
            from_id=from_id,
            to_id=to_id,
            scope=scope,
            visibility=visibility,
            source_event_ids=source_event_ids,
            notes=notes,
        )

    # Service layer
    def apply_mined_output(
        self,
        *,
        output: dict[str, Any],
        allowed_event_ids: set[str],
        min_confidence: float,
        max_claims: int,
    ) -> dict[str, Any]:
        return self._service.apply_mined_output(
            output=output,
            allowed_event_ids=allowed_event_ids,
            min_confidence=min_confidence,
            max_claims=max_claims,
        )
