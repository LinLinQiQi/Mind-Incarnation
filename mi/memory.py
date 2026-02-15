from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .paths import GlobalPaths, ProjectPaths
from .storage import ensure_dir, iter_jsonl, now_rfc3339, read_json
from .workflows import render_workflow_markdown


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


def _json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except Exception:
        return "{}"


def _safe_list_str(items: Any, *, limit: int) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for x in items:
        if len(out) >= limit:
            break
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _tokenize_query(text: str) -> list[str]:
    # Keep it conservative: portable across sqlite FTS versions and safe for user input.
    toks = re.findall(r"[A-Za-z0-9_./:-]{2,}", (text or "").lower())
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:24]


class MemoryIndex:
    """A small text index for MemoryItems (best-effort, sqlite-backed).

    This is a materialized view only; the source of truth remains MI's event ledger
    and workflow/learned stores under MI_HOME.
    """

    def __init__(self, home_dir: Path) -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._paths = GlobalPaths(home_dir=self._home_dir)
        self._db_path = self._paths.indexes_dir / "memory.sqlite"

    @property
    def db_path(self) -> Path:
        return self._db_path

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
            cur.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(item_id UNINDEXED, title, body, tags)"
            )
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

    def search(
        self,
        *,
        query: str,
        top_k: int,
        kinds: set[str],
        include_global: bool,
        exclude_project_id: str,
    ) -> list[MemoryItem]:
        toks = _tokenize_query(query)
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


def _iter_project_ids(home_dir: Path) -> Iterable[str]:
    projects = Path(home_dir).expanduser().resolve() / "projects"
    if not projects.is_dir():
        return []
    out: list[str] = []
    for d in sorted(projects.iterdir()):
        if d.is_dir() and d.name and not d.name.startswith("."):
            out.append(d.name)
    return out


def _learned_items_for_file(*, learned_path: Path, scope: str, project_id: str) -> list[MemoryItem]:
    disables: set[str] = set()
    for entry in iter_jsonl(learned_path):
        if not isinstance(entry, dict):
            continue
        if entry.get("action") == "disable" and entry.get("target_id"):
            disables.add(str(entry["target_id"]))

    out: list[MemoryItem] = []
    for entry in iter_jsonl(learned_path):
        if not isinstance(entry, dict):
            continue
        if entry.get("action") == "disable":
            continue
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id or entry_id in disables:
            continue
        if not bool(entry.get("enabled", True)):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        ts = str(entry.get("ts") or "").strip() or now_rfc3339()
        rationale = str(entry.get("rationale") or "").strip()
        title = text
        body = text + ("\n\nrationale: " + rationale if rationale else "")
        out.append(
            MemoryItem(
                item_id=f"learned:{scope}:{project_id or 'global'}:{entry_id}",
                kind="learned",
                scope=scope,
                project_id=project_id,
                ts=ts,
                title=_truncate(title, 120),
                body=_truncate(body, 2400),
                tags=["learned", scope],
                source_refs=[{"kind": "learned_file", "path": str(learned_path), "entry_id": entry_id}],
            )
        )
    return out


def _workflow_items_for_dir(*, workflows_dir: Path, scope: str, project_id: str) -> list[MemoryItem]:
    out: list[MemoryItem] = []
    try:
        paths = sorted(workflows_dir.glob("wf_*.json"))
    except Exception:
        return []
    for p in paths:
        obj = read_json(p, default=None)
        if not isinstance(obj, dict):
            continue
        if not bool(obj.get("enabled", False)):
            continue
        wid = str(obj.get("id") or "").strip() or p.stem
        name = str(obj.get("name") or "").strip() or wid
        ts = str(obj.get("updated_ts") or obj.get("created_ts") or "").strip() or now_rfc3339()
        trig = obj.get("trigger") if isinstance(obj.get("trigger"), dict) else {}
        mode = str(trig.get("mode") or "").strip()
        pat = str(trig.get("pattern") or "").strip()

        # Index the rendered markdown: it is compact and includes steps.
        md = render_workflow_markdown(obj)
        body = "\n".join(
            [
                f"name: {name}",
                f"id: {wid}",
                (f"trigger: {mode} {pat}".strip() if mode or pat else "trigger: (none)"),
                "",
                md.strip(),
            ]
        ).strip()

        tags = ["workflow", scope]
        if mode:
            tags.append("trigger:" + mode)
        out.append(
            MemoryItem(
                item_id=f"workflow:{scope}:{project_id or 'global'}:{wid}",
                kind="workflow",
                scope=scope,
                project_id=project_id,
                ts=ts,
                title=_truncate(name, 140),
                body=_truncate(body, 6000),
                tags=tags,
                source_refs=[{"kind": "workflow_file", "path": str(p), "workflow_id": wid}],
            )
        )
    return out


def ingest_learned_and_workflows(*, home_dir: Path, index: MemoryIndex) -> None:
    """Best-effort ingestion for small structured stores (no event log scanning)."""

    gp = GlobalPaths(home_dir=Path(home_dir).expanduser().resolve())
    items: list[MemoryItem] = []

    # Global learned.
    items.extend(_learned_items_for_file(learned_path=gp.learned_path, scope="global", project_id=""))

    # Global workflows.
    wf_global_dir = gp.global_workflows_dir
    items.extend(_workflow_items_for_dir(workflows_dir=wf_global_dir, scope="global", project_id=""))

    # Per-project learned + workflows.
    for pid in _iter_project_ids(gp.home_dir):
        pp = ProjectPaths(home_dir=gp.home_dir, project_root=Path("."), _project_id=pid)  # project_root unused when _project_id provided
        items.extend(_learned_items_for_file(learned_path=pp.learned_path, scope="project", project_id=pid))
        items.extend(_workflow_items_for_dir(workflows_dir=pp.workflows_dir, scope="project", project_id=pid))

    index.upsert_items(items)


def build_snapshot_item(
    *,
    project_id: str,
    segment_id: str,
    thread_id: str,
    batch_id: str,
    task_hint: str,
    checkpoint_kind: str,
    status_hint: str,
    checkpoint_notes: str,
    segment_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], MemoryItem]:
    """Build a snapshot event + a MemoryItem for indexing (deterministic; no extra model call)."""

    facts: list[str] = []
    results: list[str] = []
    actions: list[str] = []
    unknowns: list[str] = []
    risk: list[str] = []
    recall: list[str] = []
    workflows: list[str] = []

    batch_ids: list[str] = []
    seen_batch: set[str] = set()
    for rec in segment_records or []:
        if not isinstance(rec, dict):
            continue
        bid = str(rec.get("batch_id") or "").strip()
        if bid and bid not in seen_batch:
            seen_batch.add(bid)
            batch_ids.append(bid)

        k = str(rec.get("kind") or "").strip()
        if k == "evidence":
            facts.extend(_safe_list_str(rec.get("facts"), limit=12))
            actions.extend(_safe_list_str(rec.get("actions"), limit=12))
            results.extend(_safe_list_str(rec.get("results"), limit=12))
            unknowns.extend(_safe_list_str(rec.get("unknowns"), limit=12))
            risk.extend(_safe_list_str(rec.get("risk_signals"), limit=8))
        elif k == "risk_event":
            cat = str(rec.get("category") or "").strip()
            sev = str(rec.get("severity") or "").strip()
            rs = _safe_list_str(rec.get("risk_signals"), limit=8)
            if cat or sev:
                risk.append(f"risk_event: {cat} severity={sev}".strip())
            for x in rs:
                risk.append(x)
        elif k == "cross_project_recall":
            recall.extend(_safe_list_str(rec.get("items"), limit=6))
        elif k == "workflow_trigger":
            wid = str(rec.get("workflow_id") or "").strip()
            name = str(rec.get("workflow_name") or "").strip()
            workflows.append(f"{wid} {name}".strip())

    # Dedup while preserving order.
    def dedup(xs: list[str], limit: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for x in xs:
            if len(out) >= limit:
                break
            s = str(x or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    facts = dedup(facts, 12)
    actions = dedup(actions, 12)
    results = dedup(results, 12)
    unknowns = dedup(unknowns, 10)
    risk = dedup(risk, 10)
    workflows = dedup(workflows, 6)

    tags = ["snapshot", checkpoint_kind or "other"]
    if status_hint:
        tags.append("status:" + status_hint)
    if workflows:
        tags.append("workflow")
    if risk:
        tags.append("risk")

    bullets: list[str] = []
    if workflows:
        bullets.append("workflows: " + "; ".join(workflows))
    if facts:
        bullets.append("facts: " + "; ".join(facts))
    if actions:
        bullets.append("actions: " + "; ".join(actions))
    if results:
        bullets.append("results: " + "; ".join(results))
    if unknowns:
        bullets.append("unknowns: " + "; ".join(unknowns))
    if risk:
        bullets.append("risk: " + "; ".join(risk))
    if recall:
        bullets.append("recall: " + "; ".join(dedup(recall, 6)))
    if checkpoint_notes:
        bullets.append("checkpoint_notes: " + _truncate(checkpoint_notes.strip(), 240))

    title = _truncate(task_hint.strip() or "segment snapshot", 140)
    body = "\n".join([f"- {b}" for b in bullets if b.strip()]).strip()
    if not body:
        body = "- (no salient events captured)"

    snap_event: dict[str, Any] = {
        "kind": "snapshot",
        "ts": now_rfc3339(),
        "thread_id": thread_id,
        "project_id": project_id,
        "segment_id": segment_id,
        "batch_id": batch_id,
        "checkpoint_kind": checkpoint_kind,
        "status_hint": status_hint,
        "task_hint": _truncate(task_hint, 200),
        "tags": tags,
        "text": _truncate(body, 8000),
        "source_refs": [
            {
                "kind": "segment_records",
                "segment_id": segment_id,
                "batch_ids": batch_ids[:40],
            }
        ],
    }

    item = MemoryItem(
        item_id=f"snapshot:project:{project_id}:{segment_id}:{batch_id}",
        kind="snapshot",
        scope="project",
        project_id=project_id,
        ts=snap_event["ts"],
        title=title,
        body=_truncate(body, 8000),
        tags=tags,
        source_refs=snap_event["source_refs"],
    )
    return snap_event, item


def render_recall_context(
    *,
    items: list[MemoryItem],
    max_chars: int,
) -> tuple[list[dict[str, Any]], str]:
    """Render a compact recall context (for EvidenceLog + prompt injection)."""

    rendered: list[dict[str, Any]] = []
    lines: list[str] = []
    lines.append("[Cross-Project Recall]")
    if not items:
        lines.append("(none)")
        return rendered, "\n".join(lines).strip() + "\n"

    budget = max(200, int(max_chars))
    used = 0
    for it in items:
        proj = it.project_id if it.scope == "project" else "global"
        head = f"- ({it.kind}/{it.scope} from {proj} @ {it.ts}) {it.title}".strip()
        snippet = _truncate(it.body.strip().replace("\n", " "), 320)
        block = head + ("\n  " + snippet if snippet else "")
        if used + len(block) > budget and rendered:
            break
        used += len(block) + 1
        lines.append(block)
        rendered.append(
            {
                "item_id": it.item_id,
                "kind": it.kind,
                "scope": it.scope,
                "project_id": it.project_id,
                "ts": it.ts,
                "title": it.title,
                "snippet": snippet,
                "tags": it.tags,
                "source_refs": it.source_refs,
            }
        )
    return rendered, "\n".join(lines).strip() + "\n"
