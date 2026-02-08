from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _dedup_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _looks_like_path(s: str) -> bool:
    s = (s or "").strip()
    if not s or "\n" in s:
        return False
    if len(s) > 240:
        return False
    lower = s.lower()
    if lower.startswith(("http://", "https://")):
        return False
    # Cheap heuristic: most repo paths include a separator or an extension.
    if "/" in s or "\\" in s:
        return True
    if "." in s and not s.startswith(".") and " " not in s:
        return True
    return False


def _collect_paths(obj: Any, *, limit: int, depth: int) -> list[str]:
    if limit <= 0 or depth <= 0:
        return []

    out: list[str] = []

    def add(s: str) -> None:
        nonlocal out
        if len(out) >= limit:
            return
        s2 = (s or "").strip()
        if _looks_like_path(s2):
            out.append(s2)

    if isinstance(obj, dict):
        for k, v in obj.items():
            if len(out) >= limit:
                break
            key = str(k).lower() if isinstance(k, str) else ""

            # Prefer explicit path-ish keys.
            if isinstance(v, str) and (
                key in ("path", "filepath", "file_path", "filename", "target_path", "source_path")
                or key.endswith("_path")
                or key.endswith("path")
            ):
                add(v)
                continue

            if isinstance(v, list) and key in ("paths", "files", "file_paths", "filenames"):
                for item in v:
                    if len(out) >= limit:
                        break
                    if isinstance(item, str):
                        add(item)
                continue

            # Recurse into structured values (avoid scanning large blobs).
            if isinstance(v, (dict, list)):
                out.extend(_collect_paths(v, limit=limit - len(out), depth=depth - 1))

    elif isinstance(obj, list):
        for v in obj:
            if len(out) >= limit:
                break
            if isinstance(v, (dict, list)):
                out.extend(_collect_paths(v, limit=limit - len(out), depth=depth - 1))
            elif isinstance(v, str):
                # Only accept raw strings in lists if they strongly look like paths.
                if _looks_like_path(v):
                    add(v)

    return out[:limit]


def _summarize_non_command_item(item: dict[str, Any], paths: list[str]) -> str:
    itype = str(item.get("type") or "").strip() or "unknown"
    name = ""
    for k in ("name", "tool", "tool_name", "action", "op", "operation"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break

    # Avoid including large payload fields.
    keys = []
    for k in item.keys():
        if k in ("text", "content", "diff", "patch", "stdout", "stderr", "aggregated_output"):
            continue
        keys.append(str(k))
    keys = sorted(keys)[:12]

    parts: list[str] = [f"type={itype}"]
    if name:
        parts.append(f"name={_truncate(name, 80)}")
    if paths:
        parts.append("paths=" + ",".join([_truncate(p, 80) for p in paths[:3]]))
    if keys:
        parts.append("keys=" + ",".join(keys))
    return " ".join(parts)


def summarize_codex_events(
    events: list[dict[str, Any]],
    *,
    max_paths: int = 30,
    max_non_command_actions: int = 20,
) -> dict[str, Any]:
    """Summarize Codex --json events for evidence/closure reasoning.

    This is intentionally heuristic and bounded: Codex event schemas may change,
    and we only need durable signals for MI prompts and audit.
    """

    event_type_counts: dict[str, int] = {}
    item_type_counts: dict[str, int] = {}
    file_paths: list[str] = []
    non_command_actions: list[str] = []
    errors: list[str] = []

    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_type = str(ev.get("type") or "").strip()
        if ev_type:
            event_type_counts[ev_type] = event_type_counts.get(ev_type, 0) + 1

        if ev_type in ("error", "thread.error"):
            msg = ev.get("message") or ev.get("error") or ev.get("detail") or ""
            if isinstance(msg, str) and msg.strip():
                errors.append(_truncate(msg.strip(), 400))

        if ev_type != "item.completed":
            continue
        item = ev.get("item")
        if not isinstance(item, dict):
            continue
        itype = str(item.get("type") or "").strip()
        if itype:
            item_type_counts[itype] = item_type_counts.get(itype, 0) + 1

        paths = _collect_paths(item, limit=max_paths - len(file_paths), depth=5)
        if paths:
            file_paths.extend(paths)

        if itype and itype not in ("command_execution", "agent_message"):
            if len(non_command_actions) < max_non_command_actions:
                non_command_actions.append(_summarize_non_command_item(item, paths))

    file_paths = _dedup_preserve(file_paths)[:max_paths]
    errors = _dedup_preserve(errors)[:5]

    return {
        "event_type_counts": event_type_counts,
        "item_type_counts": item_type_counts,
        "file_paths": file_paths,
        "non_command_actions": non_command_actions[:max_non_command_actions],
        "errors": errors,
    }


def last_agent_message_from_transcript(transcript_path: Path, *, limit_chars: int = 8000) -> str:
    """Extract the last agent_message text from a Hands transcript JSONL file.

    Transcript format is MI-owned: each line is JSON with {ts, stream, line},
    where stream=stdout may contain Codex JSON events in the "line" field.
    """

    last = ""
    try:
        with transcript_path.open("r", encoding="utf-8") as f:
            for row in f:
                row = row.strip()
                if not row:
                    continue
                try:
                    rec = json.loads(row)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("stream") != "stdout":
                    continue
                raw = rec.get("line")
                if not isinstance(raw, str):
                    continue
                s = raw.strip()
                if not (s.startswith("{") and s.endswith("}")):
                    continue
                try:
                    ev = json.loads(s)
                except Exception:
                    continue
                if ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict):
                    item = ev["item"]
                    if item.get("type") == "agent_message":
                        last = str(item.get("text") or "")
    except FileNotFoundError:
        return ""

    return _truncate(last, limit_chars)
