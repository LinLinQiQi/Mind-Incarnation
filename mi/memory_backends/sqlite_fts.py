from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from ..memory_text import tokenize_query
from ..memory_types import MemoryGroup, MemoryItem
from ..paths import GlobalPaths
from ..storage import ensure_dir


def _json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except Exception:
        return "{}"


def _chunks(items: list[str], *, size: int) -> Iterator[list[str]]:
    if size <= 0:
        size = 200
    for i in range(0, len(items), size):
        yield items[i : i + size]


class SqliteFtsBackend:
    """SQLite-backed text index (FTS5/FTS4 best-effort)."""

    name = "sqlite_fts"

    def __init__(self, home_dir: Path) -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._paths = GlobalPaths(home_dir=self._home_dir)
        self._db_path = self._paths.indexes_dir / "memory.sqlite"

    @property
    def db_path(self) -> Path:
        return self._db_path

    def reset(self) -> None:
        try:
            if self._db_path.exists():
                self._db_path.unlink()
        except Exception:
            return

    def _connect(self) -> sqlite3.Connection:
        ensure_dir(self._db_path.parent)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> str:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
              item_id TEXT PRIMARY KEY,
              kind TEXT,
              scope TEXT,
              project_id TEXT,
              ts TEXT,
              title TEXT,
              body TEXT,
              tags TEXT,
              source_refs TEXT
            )
            """
        )

        row = cur.execute("SELECT value FROM meta WHERE key='fts_version'").fetchone()
        if row and str(row["value"] or "").strip():
            return str(row["value"])

        # Prefer FTS5 when available; fall back to FTS4; otherwise fall back to LIKE scanning.
        fts_version = "none"
        try:
            cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(item_id UNINDEXED, title, body, tags)")
            fts_version = "fts5"
        except Exception:
            try:
                cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts4(item_id, title, body, tags)")
                fts_version = "fts4"
            except Exception:
                fts_version = "none"

        cur.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fts_version',?)", (fts_version,))
        conn.commit()
        return fts_version

    def upsert_items(self, items: list[MemoryItem]) -> None:
        if not items:
            return
        try:
            conn = self._connect()
            try:
                fts = self._ensure_schema(conn)
                cur = conn.cursor()
                for it in items:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO items(item_id,kind,scope,project_id,ts,title,body,tags,source_refs)
                        VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            it.item_id,
                            it.kind,
                            it.scope,
                            it.project_id,
                            it.ts,
                            it.title,
                            it.body,
                            _json_dumps(it.tags),
                            _json_dumps(it.source_refs),
                        ),
                    )
                    if fts in ("fts5", "fts4"):
                        cur.execute("DELETE FROM items_fts WHERE item_id=?", (it.item_id,))
                        cur.execute(
                            "INSERT INTO items_fts(item_id,title,body,tags) VALUES(?,?,?,?)",
                            (it.item_id, it.title, it.body, " ".join(it.tags)),
                        )
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            # Best-effort: indexing must never break MI runs.
            return

    def sync_groups(self, groups: list[MemoryGroup], *, existing_project_ids: set[str] | None = None) -> None:
        """Sync groups into the index and prune stale learned/workflow items."""

        if not groups and existing_project_ids is None:
            return

        try:
            conn = self._connect()
            try:
                fts = self._ensure_schema(conn)
                cur = conn.cursor()

                def delete_ids(ids: list[str]) -> None:
                    if not ids:
                        return
                    for chunk in _chunks(ids, size=400):
                        qs = ",".join(["?"] * len(chunk))
                        cur.execute(f"DELETE FROM items WHERE item_id IN ({qs})", chunk)
                        if fts in ("fts5", "fts4"):
                            cur.execute(f"DELETE FROM items_fts WHERE item_id IN ({qs})", chunk)

                for g in groups:
                    kind = str(g.kind or "").strip()
                    scope = str(g.scope or "").strip()
                    pid = str(g.project_id or "").strip()
                    keep_ids = {str(it.item_id or "").strip() for it in g.items if str(it.item_id or "").strip()}

                    # Upsert all group items.
                    for it in g.items:
                        cur.execute(
                            """
                            INSERT OR REPLACE INTO items(item_id,kind,scope,project_id,ts,title,body,tags,source_refs)
                            VALUES(?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                it.item_id,
                                it.kind,
                                it.scope,
                                it.project_id,
                                it.ts,
                                it.title,
                                it.body,
                                _json_dumps(it.tags),
                                _json_dumps(it.source_refs),
                            ),
                        )
                        if fts in ("fts5", "fts4"):
                            cur.execute("DELETE FROM items_fts WHERE item_id=?", (it.item_id,))
                            cur.execute(
                                "INSERT INTO items_fts(item_id,title,body,tags) VALUES(?,?,?,?)",
                                (it.item_id, it.title, it.body, " ".join(it.tags)),
                            )

                    # Prune: delete any items in this group that are no longer present.
                    if kind and scope:
                        rows = cur.execute(
                            "SELECT item_id FROM items WHERE kind=? AND scope=? AND project_id=?",
                            (kind, scope, pid),
                        ).fetchall()
                        existing_ids = [str(r[0] or "") for r in rows if r and str(r[0] or "").strip()]
                        stale = [x for x in existing_ids if x not in keep_ids]
                        delete_ids(stale)

                # Prune orphaned project-scoped learned/workflow items when projects were deleted.
                if existing_project_ids is not None:
                    keep = sorted({str(x).strip() for x in existing_project_ids if str(x).strip()})
                    if keep:
                        qs = ",".join(["?"] * len(keep))
                        rows = cur.execute(
                            f"""
                            SELECT item_id FROM items
                            WHERE scope='project'
                              AND kind IN ('learned','workflow','claim')
                              AND project_id NOT IN ({qs})
                            """,
                            keep,
                        ).fetchall()
                    else:
                        rows = cur.execute(
                            """
                            SELECT item_id FROM items
                            WHERE scope='project' AND kind IN ('learned','workflow','claim')
                            """
                        ).fetchall()
                    orphan_ids = [str(r[0] or "") for r in rows if r and str(r[0] or "").strip()]
                    delete_ids(orphan_ids)

                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            # Best-effort: indexing must never break MI runs.
            return

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

        # Build a conservative FTS query: space-separated tokens (AND).
        fts_query = " ".join(toks)
        kind_list = sorted({k for k in kinds if k})

        try:
            conn = self._connect()
            try:
                fts = self._ensure_schema(conn)
                cur = conn.cursor()

                where: list[str] = []
                params: list[Any] = []
                if kind_list:
                    where.append("items.kind IN (" + ",".join(["?"] * len(kind_list)) + ")")
                    params.extend(kind_list)
                if exclude_project_id:
                    where.append("(items.scope='global' OR items.project_id!=?)")
                    params.append(exclude_project_id)
                if not include_global:
                    where.append("items.scope!='global'")
                where_sql = " AND ".join(where) if where else "1=1"

                if fts in ("fts5", "fts4"):
                    order_sql = "bm25(items_fts)" if fts == "fts5" else "items.ts DESC"
                    rows = cur.execute(
                        f"""
                        SELECT items.item_id, items.kind, items.scope, items.project_id, items.ts, items.title, items.body, items.tags, items.source_refs
                        FROM items_fts JOIN items ON items.item_id = items_fts.item_id
                        WHERE items_fts MATCH ? AND {where_sql}
                        ORDER BY {order_sql}
                        LIMIT ?
                        """,
                        [fts_query, *params, int(top_k)],
                    ).fetchall()
                else:
                    # Fallback: LIKE scan over a bounded subset.
                    like = "%" + toks[0] + "%"
                    rows = cur.execute(
                        f"""
                        SELECT item_id, kind, scope, project_id, ts, title, body, tags, source_refs
                        FROM items
                        WHERE {where_sql} AND (lower(title) LIKE ? OR lower(body) LIKE ?)
                        ORDER BY ts DESC
                        LIMIT ?
                        """,
                        [*params, like, like, int(top_k)],
                    ).fetchall()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            return []

        out: list[MemoryItem] = []
        for r in rows or []:
            try:
                tags = json.loads(r["tags"] or "[]")
            except Exception:
                tags = []
            try:
                refs = json.loads(r["source_refs"] or "[]")
            except Exception:
                refs = []
            out.append(
                MemoryItem(
                    item_id=str(r["item_id"] or ""),
                    kind=str(r["kind"] or ""),
                    scope=str(r["scope"] or ""),
                    project_id=str(r["project_id"] or ""),
                    ts=str(r["ts"] or ""),
                    title=str(r["title"] or ""),
                    body=str(r["body"] or ""),
                    tags=[str(x) for x in tags if str(x).strip()] if isinstance(tags, list) else [],
                    source_refs=[x for x in refs if isinstance(x, dict)] if isinstance(refs, list) else [],
                )
            )
        return out

    def status(self) -> dict[str, Any]:
        """Return a best-effort status summary (without raising)."""

        out: dict[str, Any] = {
            "backend": self.name,
            "db_path": str(self._db_path),
            "exists": bool(self._db_path.exists()),
            "fts_version": "unknown",
            "total_items": 0,
            "groups": [],
        }
        if not out["exists"]:
            return out

        try:
            conn = self._connect()
            try:
                fts = self._ensure_schema(conn)
                out["fts_version"] = fts
                cur = conn.cursor()
                row = cur.execute("SELECT COUNT(*) AS n FROM items").fetchone()
                out["total_items"] = int(row["n"] if row and row["n"] is not None else 0)
                rows = cur.execute(
                    """
                    SELECT kind, scope, project_id, COUNT(*) AS n
                    FROM items
                    GROUP BY kind, scope, project_id
                    ORDER BY kind, scope, project_id
                    """
                ).fetchall()
                groups: list[dict[str, Any]] = []
                for r in rows or []:
                    groups.append(
                        {
                            "kind": str(r["kind"] or ""),
                            "scope": str(r["scope"] or ""),
                            "project_id": str(r["project_id"] or ""),
                            "count": int(r["n"] if r["n"] is not None else 0),
                        }
                    )
                out["groups"] = groups
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            return out
        return out
