from __future__ import annotations

import gzip
import json
import re
from collections import deque
from contextlib import contextmanager
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


def _extract_paths_from_text(text: str, *, limit: int) -> list[str]:
    if limit <= 0:
        return []
    t = (text or "").strip()
    if not t:
        return []

    # Split on common delimiters to find path-like tokens.
    tokens = re.split(r"[ \t\r\n\"'`()<>\[\]{},;:]+", t)
    out: list[str] = []
    for tok in tokens:
        if len(out) >= limit:
            break
        s = tok.strip().strip(".,")
        if not s:
            continue
        if _looks_like_path(s):
            out.append(s)
    return out[:limit]


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


_ARCHIVE_STUB_TYPE = "mi.transcript.archived"


def resolve_transcript_path(transcript_path: Path) -> Path:
    """Resolve a transcript path to its underlying storage path.

    If the path is an archive stub (type=mi.transcript.archived), return the
    archived_path when it exists; otherwise return the original path.
    """

    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                s = line.strip()
                if not (s.startswith("{") and s.endswith("}")):
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if str(obj.get("type") or "") != _ARCHIVE_STUB_TYPE:
                    continue
                ap = obj.get("archived_path")
                if isinstance(ap, str) and ap.strip():
                    cand = Path(ap.strip()).expanduser()
                    if cand.exists():
                        return cand
                break
    except FileNotFoundError:
        return transcript_path
    except Exception:
        return transcript_path

    return transcript_path


@contextmanager
def open_transcript_text(transcript_path: Path):
    """Open a transcript for reading as text, following archive stubs and .gz files."""

    real = resolve_transcript_path(transcript_path)
    if real.suffix == ".gz":
        f = gzip.open(real, "rt", encoding="utf-8", errors="replace")
    else:
        f = real.open("r", encoding="utf-8", errors="replace")
    try:
        yield f
    finally:
        try:
            f.close()
        except Exception:
            pass


def tail_transcript_lines(transcript_path: Path, n: int) -> list[str]:
    if n <= 0:
        return []
    dq: deque[str] = deque(maxlen=n)
    try:
        with open_transcript_text(transcript_path) as f:
            for line in f:
                dq.append(line.rstrip("\n"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return list(dq)


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


def summarize_hands_transcript(
    transcript_path: Path,
    *,
    max_paths: int = 30,
    max_non_command_actions: int = 20,
    max_errors: int = 5,
) -> dict[str, Any]:
    """Summarize an MI-owned Hands transcript JSONL file (stdout/stderr lines).

    This is used for non-Codex Hands providers (e.g., wrapping another agent CLI),
    where we do not have Codex's structured --json event stream.
    """

    event_type_counts: dict[str, int] = {}
    item_type_counts: dict[str, int] = {}
    file_paths: list[str] = []
    non_command_actions: list[str] = []
    errors: list[str] = []
    session_id = ""

    stdout_lines = 0
    stderr_lines = 0
    meta_lines = 0

    def add_action(s: str) -> None:
        if len(non_command_actions) >= max_non_command_actions:
            return
        s2 = (s or "").strip()
        if s2:
            non_command_actions.append(_truncate(s2, 200))

    def maybe_parse_event(line: str) -> dict[str, Any] | None:
        s = (line or "").strip()
        if not (s.startswith("{") and s.endswith("}")):
            return None
        try:
            obj = json.loads(s)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    try:
        with open_transcript_text(transcript_path) as f:
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
                stream = rec.get("stream")
                if stream not in ("stdout", "stderr", "meta"):
                    continue
                line = rec.get("line")
                s = str(line) if line is not None else ""
                s2 = s.strip()

                key = f"stream.{stream}"
                event_type_counts[key] = event_type_counts.get(key, 0) + 1

                if stream == "stdout":
                    stdout_lines += 1
                elif stream == "stderr":
                    stderr_lines += 1
                else:
                    meta_lines += 1

                # Claude Code's stream-json is common for CLI Hands. Parse JSON events when possible.
                ev = maybe_parse_event(s2) if stream in ("stdout", "stderr") else None
                if ev:
                    et = str(ev.get("type") or "").strip()
                    if et:
                        event_type_counts[f"event.{et}"] = event_type_counts.get(f"event.{et}", 0) + 1

                    if not session_id:
                        sid = ev.get("session_id") or ev.get("sessionId") or ""
                        if isinstance(sid, str) and sid.strip():
                            session_id = sid.strip()

                    # Stream wrapper: count nested raw event types too.
                    if et == "stream_event":
                        inner = ev.get("event")
                        if isinstance(inner, dict):
                            it = str(inner.get("type") or "").strip()
                            if it:
                                item_type_counts[it] = item_type_counts.get(it, 0) + 1
                    # Extract file paths by structured traversal (less brittle than token scanning).
                    if len(file_paths) < max_paths:
                        file_paths.extend(_collect_paths(ev, limit=max_paths - len(file_paths), depth=6))

                    # Record a small number of human-friendly event summaries.
                    subtype = ev.get("subtype")
                    st = str(subtype).strip() if isinstance(subtype, str) else ""
                    if et:
                        add_action(f"type={et}" + (f" subtype={st}" if st else ""))

                    # Extract common error signals.
                    if len(errors) < max_errors:
                        lower = s2.lower()
                        if et == "error":
                            errors.append(_truncate(s2, 400))
                        elif et == "result":
                            if st and st not in ("success", "ok"):
                                errors.append(_truncate(s2, 400))
                        elif any(x in lower for x in ("traceback", "exception", "error:", "failed", "fatal", "panic")):
                            errors.append(_truncate(s2, 400))
                else:
                    # Plain text output: tokenize for path-ish strings.
                    if s2 and len(file_paths) < max_paths:
                        file_paths.extend(_extract_paths_from_text(s2, limit=max_paths - len(file_paths)))

                if len(errors) < max_errors:
                    lower = s2.lower()
                    if stream == "stderr" and s2:
                        errors.append(_truncate(s2, 400))
                    elif any(x in lower for x in ("traceback", "exception", "error:", "failed", "fatal", "panic")):
                        errors.append(_truncate(s2, 400))

    except FileNotFoundError:
        return {
            "event_type_counts": {},
            "item_type_counts": {},
            "file_paths": [],
            "non_command_actions": [],
            "errors": [],
        }

    file_paths = _dedup_preserve(file_paths)[:max_paths]
    errors = _dedup_preserve(errors)[:max_errors]

    # Include basic line counts at the start (always available).
    non_command_actions = (
        [
            f"raw_transcript_lines={stdout_lines + stderr_lines + meta_lines}",
            f"stdout_lines={stdout_lines}",
            f"stderr_lines={stderr_lines}",
        ]
        + non_command_actions
    )[:max_non_command_actions]

    if session_id:
        add_action(f"session_id={session_id}")

    return {
        "event_type_counts": event_type_counts,
        "item_type_counts": item_type_counts,
        "file_paths": file_paths,
        "non_command_actions": non_command_actions,
        "errors": errors,
    }


def last_agent_message_from_transcript(transcript_path: Path, *, limit_chars: int = 8000) -> str:
    """Extract the last agent_message text from a Hands transcript JSONL file.

    Transcript format is MI-owned: each line is JSON with {ts, stream, line},
    where stream=stdout may contain Codex JSON events in the "line" field.
    """

    last = ""
    last_stdout_line = ""
    claude_result = ""
    claude_assistant = ""
    # Bounded accumulation for stream-json text deltas.
    stream_frags: deque[str] = deque()
    stream_len = 0
    stream_max = max(2000, int(limit_chars) * 2)
    try:
        with open_transcript_text(transcript_path) as f:
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
                if s:
                    last_stdout_line = s
                if not (s.startswith("{") and s.endswith("}")):
                    continue
                try:
                    ev = json.loads(s)
                except Exception:
                    continue
                if not isinstance(ev, dict):
                    continue

                # Some CLIs (e.g., Claude `--output-format json`) may emit a single JSON object without a `type`
                # field. Prefer the human `result` if present.
                if not ev.get("type") and isinstance(ev.get("result"), str) and ev.get("result").strip():
                    claude_result = ev["result"]
                    continue

                if ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict):
                    item = ev["item"]
                    if item.get("type") == "agent_message":
                        last = str(item.get("text") or "")
                        continue

                # Claude Code (and other CLIs) may output stream-json or json formats.
                et = str(ev.get("type") or "").strip()
                if et == "result" and isinstance(ev.get("result"), str):
                    claude_result = ev["result"]
                    continue
                if et == "assistant":
                    msg = ev.get("message")
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        if isinstance(content, list):
                            parts: list[str] = []
                            for blk in content:
                                if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                                    parts.append(blk["text"])
                                elif isinstance(blk, str):
                                    parts.append(blk)
                            if parts:
                                claude_assistant = "".join(parts).strip()
                                continue
                        if isinstance(content, str) and content.strip():
                            claude_assistant = content.strip()
                            continue
                if et == "stream_event":
                    inner = ev.get("event")
                    if isinstance(inner, dict):
                        delta = inner.get("delta")
                        frag = ""
                        if isinstance(delta, dict) and isinstance(delta.get("text"), str) and str(delta.get("type") or "text_delta") == "text_delta":
                            frag = delta["text"]
                        elif str(inner.get("type") or "") == "text_delta" and isinstance(inner.get("text"), str):
                            frag = inner["text"]
                        if frag:
                            stream_frags.append(frag)
                            stream_len += len(frag)
                            while stream_len > stream_max and stream_frags:
                                drop = stream_frags.popleft()
                                stream_len -= len(drop)
    except FileNotFoundError:
        return ""

    if last:
        return _truncate(last, limit_chars)
    if claude_result:
        return _truncate(claude_result, limit_chars)
    if claude_assistant:
        return _truncate(claude_assistant, limit_chars)
    if stream_frags:
        streamed = "".join(list(stream_frags)).strip()
        if streamed:
            return _truncate(streamed, limit_chars)
    return _truncate(last_stdout_line, limit_chars)
