from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def new_segment_state(
    *,
    reason: str,
    thread_hint: str,
    task: str,
    now_ts: Callable[[], str],
    truncate: Callable[[str, int], str],
    id_factory: Callable[[], str],
) -> dict[str, Any]:
    now = now_ts()
    return {
        "version": "v1",
        "open": True,
        "segment_id": id_factory(),
        "created_ts": now,
        "updated_ts": now,
        "thread_id": (thread_hint or "").strip(),
        "task_hint": truncate(str(task or "").strip(), 200),
        "reason": str(reason or "").strip(),
        "records": [],
    }


def load_segment_state(
    *,
    path: Path,
    read_json_best_effort: Callable[..., Any],
    state_warnings: list[dict[str, Any]],
    thread_hint: str,
) -> dict[str, Any] | None:
    obj = read_json_best_effort(
        path,
        default=None,
        label="segment_state",
        warnings=state_warnings,
    )
    if not isinstance(obj, dict):
        return None
    if str(obj.get("version") or "") != "v1":
        return None
    if not bool(obj.get("open", False)):
        return None
    recs = obj.get("records")
    if not isinstance(recs, list):
        obj["records"] = []

    # Basic thread affinity: only reuse when continuing the same Hands session.
    th = (thread_hint or "").strip()
    st = str(obj.get("thread_id") or "").strip()
    if th and st and th != st:
        return None
    return obj


def persist_segment_state(
    *,
    enabled: bool,
    path: Path,
    segment_state: dict[str, Any],
    segment_max_records: int,
    now_ts: Callable[[], str],
    write_json_atomic: Callable[[Path, Any], None],
) -> None:
    if not enabled:
        return
    try:
        segment_state["updated_ts"] = now_ts()
        recs = segment_state.get("records")
        if isinstance(recs, list) and len(recs) > segment_max_records:
            segment_state["records"] = recs[-segment_max_records:]
        write_json_atomic(path, segment_state)
    except Exception:
        return


def clear_segment_state(*, path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except Exception:
        return


def add_segment_record(
    *,
    enabled: bool,
    obj: dict[str, Any],
    segment_records: list[dict[str, Any]],
    segment_max_records: int,
    truncate: Callable[[str, int], str],
) -> None:
    if not enabled:
        return
    if not isinstance(obj, dict):
        return

    seg: dict[str, Any] = {}
    kind = obj.get("kind")
    if isinstance(kind, str) and kind.strip():
        seg["kind"] = kind.strip()
    bid = obj.get("batch_id")
    if isinstance(bid, str) and bid.strip():
        seg["batch_id"] = bid.strip()
    eid = obj.get("event_id")
    if isinstance(eid, str) and eid.strip():
        seg["event_id"] = eid.strip()
    seq = obj.get("seq")
    if isinstance(seq, int):
        seg["seq"] = int(seq)

    for k in ("workflow_id", "workflow_name", "trigger_mode", "trigger_pattern"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            seg[k] = truncate(v.strip(), 200)

    if obj.get("kind") == "evidence":
        seg["kind"] = "evidence"
        for k in ("facts", "actions", "results", "unknowns", "risk_signals"):
            v = obj.get(k)
            if isinstance(v, list):
                seg[k] = [str(x)[:300] for x in v[:12] if str(x).strip()]
            else:
                seg[k] = []
        repo = obj.get("repo_observation") if isinstance(obj.get("repo_observation"), dict) else {}
        if isinstance(repo, dict) and repo:
            seg["repo_observation"] = {
                "stack_hints": repo.get("stack_hints") if isinstance(repo.get("stack_hints"), list) else [],
                "has_tests": bool(repo.get("has_tests", False)),
                "git_is_repo": bool(repo.get("git_is_repo", False)),
                "git_head": truncate(str(repo.get("git_head") or ""), 120),
                "git_diff_stat": truncate(str(repo.get("git_diff_stat") or ""), 600),
                "git_diff_cached_stat": truncate(str(repo.get("git_diff_cached_stat") or ""), 600),
            }
        obs = obj.get("transcript_observation") if isinstance(obj.get("transcript_observation"), dict) else {}
        if isinstance(obs, dict) and obs:
            seg["transcript_observation"] = {
                "file_paths": (obs.get("file_paths") if isinstance(obs.get("file_paths"), list) else [])[:20],
                "errors": (obs.get("errors") if isinstance(obs.get("errors"), list) else [])[:10],
            }

    if obj.get("kind") == "risk_event":
        seg["kind"] = "risk_event"
        seg["category"] = truncate(str(obj.get("category") or ""), 60)
        seg["severity"] = truncate(str(obj.get("severity") or ""), 60)
        rs = obj.get("risk_signals") if isinstance(obj.get("risk_signals"), list) else []
        seg["risk_signals"] = [str(x)[:200] for x in rs[:8] if str(x).strip()]

    if obj.get("kind") == "check_plan":
        seg["kind"] = "check_plan"
        seg["should_run_checks"] = bool(obj.get("should_run_checks", False))
        seg["needs_testless_strategy"] = bool(obj.get("needs_testless_strategy", False))
        seg["notes"] = truncate(str(obj.get("notes") or ""), 200)

    if obj.get("kind") == "auto_answer":
        seg["kind"] = "auto_answer"
        seg["should_answer"] = bool(obj.get("should_answer", False))
        seg["needs_user_input"] = bool(obj.get("needs_user_input", False))
        seg["ask_user_question"] = truncate(str(obj.get("ask_user_question") or ""), 200)

    if obj.get("kind") == "decide_next":
        seg["kind"] = "decide_next"
        seg["next_action"] = truncate(str(obj.get("next_action") or ""), 40)
        seg["status"] = truncate(str(obj.get("status") or ""), 40)
        seg["notes"] = truncate(str(obj.get("notes") or ""), 200)

    if obj.get("kind") == "user_input":
        seg["kind"] = "user_input"
        seg["question"] = truncate(str(obj.get("question") or ""), 200)
        seg["answer"] = truncate(str(obj.get("answer") or ""), 200)

    if obj.get("kind") == "cross_project_recall":
        seg["kind"] = "cross_project_recall"
        seg["reason"] = truncate(str(obj.get("reason") or ""), 60)
        seg["query"] = truncate(str(obj.get("query") or ""), 200)
        items = obj.get("items") if isinstance(obj.get("items"), list) else []
        names: list[str] = []
        for it in items:
            if len(names) >= 6:
                break
            if isinstance(it, dict):
                k = str(it.get("kind") or "").strip()
                sc = str(it.get("scope") or "").strip()
                title = str(it.get("title") or "").strip()
                head = (f"{k}/{sc} {title}").strip()
                if head:
                    names.append(truncate(head, 120))
            elif isinstance(it, str) and it.strip():
                names.append(truncate(it.strip(), 120))
        if names:
            seg["items"] = names

    if obj.get("kind") == "snapshot":
        seg["kind"] = "snapshot"
        seg["checkpoint_kind"] = truncate(str(obj.get("checkpoint_kind") or ""), 60)
        seg["status_hint"] = truncate(str(obj.get("status_hint") or ""), 40)
        tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
        seg["tags"] = [str(x)[:60] for x in tags[:8] if str(x).strip()]

    if seg:
        segment_records.append(seg)
        segment_records[:] = segment_records[-segment_max_records:]
