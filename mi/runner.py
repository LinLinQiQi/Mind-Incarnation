from __future__ import annotations

import json
import sys
import hashlib
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_runner import run_codex_exec, run_codex_resume, CodexRunResult
from .codex_runner import InterruptConfig
from .llm import MiLlm
from .mind_errors import MindCallError
from .mindspec import MindSpecStore
from .paths import GlobalPaths, ProjectPaths, default_home_dir
from .prompts import decide_next_prompt, extract_evidence_prompt, plan_min_checks_prompt, workflow_progress_prompt, checkpoint_decide_prompt
from .prompts import auto_answer_to_codex_prompt
from .prompts import risk_judge_prompt
from .prompts import suggest_workflow_prompt, mine_preferences_prompt
from .risk import detect_risk_signals_from_command, detect_risk_signals_from_text_line
from .storage import append_jsonl, now_rfc3339, ensure_dir, read_json, write_json
from .transcript import summarize_codex_events, summarize_hands_transcript, open_transcript_text
from .workflows import (
    WorkflowStore,
    GlobalWorkflowStore,
    WorkflowRegistry,
    load_workflow_candidates,
    write_workflow_candidates,
    new_workflow_id,
    render_workflow_markdown,
)
from .preferences import load_preference_candidates, write_preference_candidates, preference_signature
from .hosts import sync_hosts_from_overlay
from .memory import MemoryIndex, ingest_learned_and_workflows, build_snapshot_item, render_recall_context


_DEFAULT = object()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _batch_summary(result: CodexRunResult) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    for item in result.iter_command_executions():
        commands.append(
            {
                "command": str(item.get("command") or ""),
                "exit_code": item.get("exit_code"),
                "output": _truncate(str(item.get("aggregated_output") or ""), 2000),
            }
        )

    transcript_observation: dict[str, Any]
    if isinstance(getattr(result, "events", None), list) and result.events:
        transcript_observation = summarize_codex_events(result.events)
    else:
        tp = getattr(result, "raw_transcript_path", None)
        transcript_observation = summarize_hands_transcript(Path(tp)) if tp else {}

    return {
        "thread_id": result.thread_id,
        "exit_code": result.exit_code,
        "commands": commands,
        "transcript_observation": transcript_observation,
        "last_agent_message": _truncate(result.last_agent_message(), 4000),
    }


def _detect_risk_signals(result: CodexRunResult) -> list[str]:
    signals: list[str] = []
    for item in result.iter_command_executions():
        cmd = str(item.get("command") or "")
        signals.extend(detect_risk_signals_from_command(cmd))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for s in signals:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _detect_risk_signals_from_transcript(transcript_path: Path) -> list[str]:
    # Best-effort for non-Codex Hands providers: scan raw stdout/stderr text for risky markers.
    signals: list[str] = []
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
                if rec.get("stream") not in ("stdout", "stderr"):
                    continue
                raw = rec.get("line")
                if not isinstance(raw, str):
                    continue
                line = raw.strip()
                if not line:
                    continue
                signals.extend(detect_risk_signals_from_text_line(line, limit=200))

                if len(signals) >= 20:
                    break
    except Exception:
        return []

    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for s in signals:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _looks_like_user_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lower = t.lower()
    if "?" in t:
        return True
    # Heuristic patterns; keep conservative to avoid extra model calls.
    patterns = [
        "do you want",
        "would you like",
        "should i",
        "shall i",
        "can i",
        "may i",
        "please confirm",
        "please provide",
        "which option",
        "choose one",
        "pick one",
        "need your",
        "need you to",
        "before i proceed",
        "to continue",
        "what should",
        "any preference",
    ]
    return any(p in lower for p in patterns)


def _empty_auto_answer() -> dict[str, Any]:
    return {
        "should_answer": False,
        "confidence": 0.0,
        "codex_answer_input": "",
        "needs_user_input": False,
        "ask_user_question": "",
        "unanswered_questions": [],
        "notes": "",
    }


def _empty_evidence_obj(*, note: str = "") -> dict[str, Any]:
    unknowns: list[str] = []
    if note.strip():
        unknowns.append(note.strip())
    return {
        "facts": [],
        "actions": [],
        "results": [],
        "unknowns": unknowns,
        "risk_signals": [],
    }

def _empty_check_plan() -> dict[str, Any]:
    return {
        "should_run_checks": False,
        "needs_testless_strategy": False,
        "testless_strategy_question": "",
        "check_goals": [],
        "commands_hints": [],
        "codex_check_input": "",
        "notes": "",
    }


def _should_plan_checks(
    *,
    summary: dict[str, Any],
    evidence_obj: dict[str, Any],
    codex_last_message: str,
    repo_observation: dict[str, Any],
) -> bool:
    # Heuristic gate to reduce mind calls; err on the side of planning checks
    # when there's uncertainty, failures, questions, or repo changes.
    try:
        if int(summary.get("exit_code") or 0) != 0:
            return True
    except Exception:
        return True

    unknowns = evidence_obj.get("unknowns") if isinstance(evidence_obj, dict) else None
    if isinstance(unknowns, list) and any(str(x).strip() for x in unknowns):
        return True

    rs = evidence_obj.get("risk_signals") if isinstance(evidence_obj, dict) else None
    if isinstance(rs, list) and any(str(x).strip() for x in rs):
        return True

    if _looks_like_user_question(codex_last_message):
        return True

    if isinstance(repo_observation, dict):
        for k in ("git_status_porcelain", "git_diff_stat", "git_diff_cached_stat"):
            v = repo_observation.get(k)
            if isinstance(v, str) and v.strip():
                return True

    return False


def _normalize_for_sig(text: str, limit: int) -> str:
    # Normalize to reduce spurious differences (whitespace/case) while keeping a bounded signature.
    t = " ".join((text or "").strip().split()).lower()
    return t[:limit]


def _loop_sig(*, codex_last_message: str, next_input: str) -> str:
    data = _normalize_for_sig(codex_last_message, 2000) + "\n---\n" + _normalize_for_sig(next_input, 2000)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _loop_pattern(sigs: list[str]) -> str:
    # Return a short pattern id when repetition suggests a stuck loop.
    if len(sigs) >= 3 and sigs[-1] == sigs[-2] == sigs[-3]:
        return "aaa"
    if len(sigs) >= 4 and sigs[-1] == sigs[-3] and sigs[-2] == sigs[-4]:
        return "abab"
    return ""


def _observe_repo(project_root: Path) -> dict[str, Any]:
    # Read-only heuristics; keep it cheap and bounded.
    root = project_root.resolve()
    stack_hints: list[str] = []
    test_hints: list[str] = []
    has_tests = False
    git_is_repo = False
    git_root = ""
    git_head = ""
    git_status_porcelain = ""
    git_diff_stat = ""
    git_diff_cached_stat = ""

    def exists(name: str) -> bool:
        return (root / name).exists()

    if exists("pyproject.toml") or exists("requirements.txt") or exists("setup.cfg") or exists("tox.ini"):
        stack_hints.append("python")
    if exists("package.json") or exists("pnpm-lock.yaml") or exists("yarn.lock"):
        stack_hints.append("node")
    if exists("go.mod"):
        stack_hints.append("go")
    if exists("Cargo.toml"):
        stack_hints.append("rust")

    # Common test configs/dirs (cheap checks).
    for name in ("pytest.ini", "tox.ini"):
        if exists(name):
            has_tests = True
            test_hints.append(name)

    for name in ("tests", "test"):
        p = root / name
        if p.is_dir():
            test_hints.append(f"{name}/")
            # Look only at immediate children to avoid large scans.
            for child in list(p.iterdir())[:200]:
                if child.is_file():
                    fn = child.name
                    if fn.startswith("test_") and fn.endswith(".py"):
                        has_tests = True
                        test_hints.append(f"{name}/{fn}")
                        break
                    if fn.endswith("_test.py"):
                        has_tests = True
                        test_hints.append(f"{name}/{fn}")
                        break

    # Node: detect a test script if package.json exists.
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            import json as _json  # local import to keep module imports minimal

            obj = _json.loads(pkg.read_text(encoding="utf-8"))
            scripts = obj.get("scripts") if isinstance(obj, dict) else None
            test_script = scripts.get("test") if isinstance(scripts, dict) else None
            if isinstance(test_script, str) and test_script.strip():
                has_tests = True
                test_hints.append("package.json scripts.test")
        except Exception:
            pass

    # Git snapshot (bounded) for closure/check reasoning.
    if shutil.which("git"):
        try:
            p = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
            git_is_repo = p.returncode == 0 and p.stdout.strip().lower() == "true"
        except Exception:
            git_is_repo = False

    def _run_git(args: list[str], *, timeout_s: float, limit: int) -> str:
        try:
            p = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            out = (p.stdout or "").strip()
            if p.returncode != 0 and not out:
                out = (p.stderr or "").strip()
            return _truncate(out, limit)
        except Exception:
            return ""

    if git_is_repo:
        git_root = _run_git(["rev-parse", "--show-toplevel"], timeout_s=1, limit=500)
        git_head = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], timeout_s=1, limit=200)
        git_status_porcelain = _run_git(["status", "--porcelain"], timeout_s=2, limit=4000)
        git_diff_stat = _run_git(["diff", "--stat"], timeout_s=2, limit=4000)
        git_diff_cached_stat = _run_git(["diff", "--cached", "--stat"], timeout_s=2, limit=4000)

    return {
        "project_root": str(root),
        "stack_hints": stack_hints,
        "has_tests": has_tests,
        "test_hints": test_hints,
        "git_is_repo": git_is_repo,
        "git_root": git_root,
        "git_head": git_head,
        "git_status_porcelain": git_status_porcelain,
        "git_diff_stat": git_diff_stat,
        "git_diff_cached_stat": git_diff_cached_stat,
    }


@dataclass(frozen=True)
class AutopilotResult:
    status: str  # done|not_done|blocked
    thread_id: str
    project_dir: Path
    evidence_log_path: Path
    transcripts_dir: Path
    batches: int
    notes: str

    def render_text(self) -> str:
        lines = [
            f"status={self.status} batches={self.batches} thread_id={self.thread_id}",
            f"project_dir={self.project_dir}",
            f"evidence_log={self.evidence_log_path}",
            f"transcripts_dir={self.transcripts_dir}",
        ]
        if self.notes:
            lines.append(f"notes={self.notes}")
        return "\n".join(lines)


def _read_user_answer(question: str) -> str:
    print(question.strip(), file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def run_autopilot(
    *,
    task: str,
    project_root: str,
    home_dir: str | None,
    max_batches: int,
    hands_exec: Any | None = None,
    hands_resume: Any = _DEFAULT,
    llm: Any | None = None,
    hands_provider: str = "",
    continue_hands: bool = False,
    reset_hands: bool = False,
) -> AutopilotResult:
    project_path = Path(project_root).resolve()
    home = Path(home_dir) if home_dir else default_home_dir()

    store = MindSpecStore(home_dir=str(home))
    loaded = store.load(project_path)

    # Cross-run Hands session persistence is stored in ProjectOverlay but only used when explicitly enabled.
    overlay: dict[str, Any]
    hands_state: dict[str, Any]
    workflow_run: dict[str, Any]

    def _refresh_overlay_refs() -> None:
        nonlocal overlay, hands_state, workflow_run
        overlay = loaded.project_overlay if isinstance(loaded.project_overlay, dict) else {}
        if not isinstance(overlay, dict):
            overlay = {}
        hs = overlay.get("hands_state")
        if isinstance(hs, dict):
            hands_state = hs
        else:
            hands_state = {}
            overlay["hands_state"] = hands_state
        wr = overlay.get("workflow_run")
        if isinstance(wr, dict):
            workflow_run = wr
        else:
            workflow_run = {}
            overlay["workflow_run"] = workflow_run

    _refresh_overlay_refs()

    cur_provider = (hands_provider or str(hands_state.get("provider") or "")).strip()
    if reset_hands:
        hands_state["provider"] = cur_provider
        hands_state["thread_id"] = ""
        hands_state["updated_ts"] = now_rfc3339()
        # Reset any best-effort workflow cursor that may be tied to the previous Hands thread.
        overlay["workflow_run"] = {}
        store.write_project_overlay(project_path, overlay)
        loaded = store.load(project_path)
        _refresh_overlay_refs()

    project_paths = ProjectPaths(home_dir=home, project_root=project_path)
    ensure_dir(project_paths.project_dir)
    ensure_dir(project_paths.transcripts_dir)

    wf_store = WorkflowStore(project_paths)
    wf_global_store = GlobalWorkflowStore(GlobalPaths(home_dir=home))
    wf_registry = WorkflowRegistry(project_store=wf_store, global_store=wf_global_store)
    mem_index = MemoryIndex(home)

    if llm is None:
        llm = MiLlm(project_root=project_path, transcripts_dir=project_paths.transcripts_dir)
    if hands_exec is None:
        hands_exec = run_codex_exec
    if hands_resume is _DEFAULT:
        hands_resume = run_codex_resume

    evidence_window: list[dict[str, Any]] = []
    thread_id: str | None = None
    resumed_from_overlay = False
    if continue_hands and not reset_hands and hands_resume is not None:
        prev_tid = str(hands_state.get("thread_id") or "").strip()
        prev_provider = str(hands_state.get("provider") or "").strip()
        if prev_tid and prev_tid != "unknown" and (not cur_provider or not prev_provider or prev_provider == cur_provider):
            thread_id = prev_tid
            resumed_from_overlay = True

    # Default: do not carry an "active" workflow cursor across runs unless we are explicitly continuing the same Hands session.
    if bool(workflow_run.get("active", False)) and not bool(resumed_from_overlay):
        workflow_run.clear()
        overlay["workflow_run"] = workflow_run
        store.write_project_overlay(project_path, overlay)
    next_input: str = task

    status = "not_done"
    notes = ""

    # Workflow trigger routing (effective): if an enabled workflow (project or global) matches the task,
    # inject it into the very first batch input (lightweight; no step slicing).
    def _match_workflow_for_task(task_text: str) -> dict[str, Any] | None:
        t = (task_text or "").lower()
        best: dict[str, Any] | None = None
        best_score = -1
        for w in wf_registry.enabled_workflows_effective(overlay=overlay):
            if not isinstance(w, dict):
                continue
            trig = w.get("trigger") if isinstance(w.get("trigger"), dict) else {}
            mode = str(trig.get("mode") or "").strip()
            pat = str(trig.get("pattern") or "").strip()
            if mode != "task_contains" or not pat:
                continue
            if pat.lower() not in t:
                continue
            score = len(pat)
            if score > best_score:
                best = w
                best_score = score
        return best

    def _workflow_step_ids(workflow: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        for s in workflow.get("steps") if isinstance(workflow.get("steps"), list) else []:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "").strip()
            if sid:
                ids.append(sid)
        return ids

    def _active_workflow() -> dict[str, Any] | None:
        if not bool(workflow_run.get("active", False)):
            return None
        wid = str(workflow_run.get("workflow_id") or "").strip()
        if not wid:
            return None
        try:
            return wf_registry.load_effective(wid)
        except Exception:
            return None

    matched = _match_workflow_for_task(task)
    if matched:
        wid = str(matched.get("id") or "").strip()
        name = str(matched.get("name") or "").strip()
        trig = matched.get("trigger") if isinstance(matched.get("trigger"), dict) else {}
        pat = str(trig.get("pattern") or "").strip()
        # Best-effort workflow cursor: internal only. It does NOT impose step-by-step reporting.
        # The cursor is used to provide next-step context to Mind prompts.
        step_ids = _workflow_step_ids(matched)
        workflow_run.clear()
        workflow_run.update(
            {
                "version": "v1",
                "active": True,
                "workflow_id": wid,
                "workflow_name": name,
                "thread_id": str(thread_id or hands_state.get("thread_id") or ""),
                "started_ts": now_rfc3339(),
                "updated_ts": now_rfc3339(),
                "completed_step_ids": [],
                "next_step_id": step_ids[0] if step_ids else "",
                "last_batch_id": "b0.workflow_trigger",
                "last_confidence": 0.0,
                "last_notes": f"triggered: task_contains pattern={pat}",
            }
        )
        overlay["workflow_run"] = workflow_run
        store.write_project_overlay(project_path, overlay)
        injected = "\n".join(
            [
                "[MI Workflow Triggered]",
                "A reusable workflow matches this task.",
                "- Use it as guidance; you do NOT need to report step-by-step.",
                "- If network/install/push/publish is not clearly safe per values, pause and ask.",
                "",
                render_workflow_markdown(matched),
                "",
                "User task:",
                task.strip(),
            ]
        ).strip()
        next_input = injected
        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "workflow_trigger",
                "batch_id": "b0.workflow_trigger",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "workflow_id": wid,
                "workflow_name": name,
                "trigger_mode": str(trig.get("mode") or ""),
                "trigger_pattern": pat,
            },
        )
        evidence_window.append(
            {
                "kind": "workflow_trigger",
                "workflow_id": wid,
                "workflow_name": name,
                "trigger_mode": str(trig.get("mode") or ""),
                "trigger_pattern": pat,
            }
        )

    # Cross-project recall (V1 default: enabled but conservative; text search only).
    recall_cfg = loaded.base.get("cross_project_recall") if isinstance(loaded.base.get("cross_project_recall"), dict) else {}
    recall_enabled = bool(recall_cfg.get("enabled", True))
    recall_triggers = recall_cfg.get("triggers") if isinstance(recall_cfg.get("triggers"), dict) else {}
    recall_run_start = bool(recall_triggers.get("run_start", True))
    recall_before_ask = bool(recall_triggers.get("before_ask_user", True))
    recall_risk_signal = bool(recall_triggers.get("risk_signal", True))
    try:
        recall_top_k = int(recall_cfg.get("top_k", 3) or 3)
    except Exception:
        recall_top_k = 3
    recall_top_k = max(1, min(10, recall_top_k))
    try:
        recall_max_chars = int(recall_cfg.get("max_chars", 1800) or 1800)
    except Exception:
        recall_max_chars = 1800
    recall_max_chars = max(200, min(6000, recall_max_chars))
    kinds_raw = recall_cfg.get("include_kinds") if isinstance(recall_cfg.get("include_kinds"), list) else ["snapshot", "learned", "workflow"]
    recall_kinds = {str(x).strip() for x in kinds_raw if str(x).strip()}
    if not recall_kinds:
        recall_kinds = {"snapshot", "learned", "workflow"}
    recall_exclude_current = bool(recall_cfg.get("exclude_current_project", True))

    # Checkpoint/segment mining settings (V1):
    # - Segments are internal; they do NOT impose a step protocol on Hands.
    # - Checkpoints decide when to mine workflows/preferences and reset the segment buffer.
    wf_cfg = loaded.base.get("workflows") if isinstance(loaded.base.get("workflows"), dict) else {}
    wf_auto_mine = bool(wf_cfg.get("auto_mine", True))
    pref_cfg = loaded.base.get("preference_mining") if isinstance(loaded.base.get("preference_mining"), dict) else {}
    pref_auto_mine = bool(pref_cfg.get("auto_mine", True))
    checkpoint_enabled = bool(wf_auto_mine or pref_auto_mine)

    segment_max_records = 40
    segment_state: dict[str, Any] = {}
    segment_records: list[dict[str, Any]] = []
    # Avoid inflating mined occurrence counts within a single `mi run` invocation.
    wf_sigs_counted_in_run: set[str] = set()
    pref_sigs_counted_in_run: set[str] = set()

    def _new_segment_state(*, reason: str, thread_hint: str) -> dict[str, Any]:
        seg_id = f"seg_{time.time_ns()}_{secrets.token_hex(4)}"
        now = now_rfc3339()
        return {
            "version": "v1",
            "open": True,
            "segment_id": seg_id,
            "created_ts": now,
            "updated_ts": now,
            "thread_id": (thread_hint or "").strip(),
            "task_hint": _truncate(task.strip(), 200),
            "reason": str(reason or "").strip(),
            "records": [],
        }

    def _load_segment_state(*, thread_hint: str) -> dict[str, Any] | None:
        obj = read_json(project_paths.segment_state_path, default=None)
        if not isinstance(obj, dict):
            return None
        if str(obj.get("version") or "") != "v1":
            return None
        if not bool(obj.get("open", False)):
            return None
        recs = obj.get("records")
        if not isinstance(recs, list):
            obj["records"] = []
        # Basic thread affinity: only reuse when continuing the same Hands session (best-effort).
        th = (thread_hint or "").strip()
        st = str(obj.get("thread_id") or "").strip()
        if th and st and th != st:
            return None
        return obj

    def _persist_segment_state() -> None:
        if not checkpoint_enabled:
            return
        try:
            segment_state["updated_ts"] = now_rfc3339()
            # Keep a bounded buffer on disk.
            recs2 = segment_state.get("records")
            if isinstance(recs2, list) and len(recs2) > segment_max_records:
                segment_state["records"] = recs2[-segment_max_records:]
            write_json(project_paths.segment_state_path, segment_state)
        except Exception:
            return

    def _clear_segment_state() -> None:
        try:
            project_paths.segment_state_path.unlink()
        except FileNotFoundError:
            return
        except Exception:
            return

    if checkpoint_enabled:
        # When NOT continuing a Hands session, do not carry over an open segment buffer.
        if not bool(continue_hands) or bool(reset_hands):
            _clear_segment_state()

        seg0 = _load_segment_state(thread_hint=str(thread_id or ""))
        segment_state = seg0 if isinstance(seg0, dict) else _new_segment_state(reason="run_start", thread_hint=str(thread_id or ""))
        recs0 = segment_state.get("records")
        segment_records = recs0 if isinstance(recs0, list) else []
        segment_state["records"] = segment_records

        # Include a workflow trigger marker in the segment when present.
        if matched:
            segment_records.append(evidence_window[-1])
            segment_records[:] = segment_records[-segment_max_records:]
        _persist_segment_state()

    intr = loaded.base.get("interrupt") or {}
    intr_mode = str(intr.get("mode") or "off")
    intr_signals = intr.get("signal_sequence") or ["SIGINT", "SIGTERM", "SIGKILL"]
    intr_escalation = intr.get("escalation_ms") or [2000, 5000]
    interrupt_cfg = (
        InterruptConfig(mode=intr_mode, signal_sequence=[str(s) for s in intr_signals], escalation_ms=[int(x) for x in intr_escalation])
        if intr_mode in ("on_high_risk", "on_any_external")
        else None
    )

    sent_sigs: list[str] = []

    # Mind circuit breaker:
    # - After repeated consecutive Mind failures, stop issuing further Mind calls
    #   in this `mi run` invocation and converge quickly to user override / blocked.
    mind_failures_total = 0
    mind_failures_consecutive = 0
    mind_circuit_open = False
    mind_circuit_threshold = 2

    def _log_decide_next(
        *,
        decision_obj: Any,
        batch_id: str,
        phase: str,
        mind_transcript_ref: str,
    ) -> None:
        if not isinstance(decision_obj, dict):
            return
        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "decide_next",
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "phase": phase,
                "next_action": str(decision_obj.get("next_action") or ""),
                "status": str(decision_obj.get("status") or ""),
                "confidence": decision_obj.get("confidence"),
                "notes": str(decision_obj.get("notes") or ""),
                "ask_user_question": str(decision_obj.get("ask_user_question") or ""),
                "next_codex_input": str(decision_obj.get("next_codex_input") or ""),
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "decision": decision_obj,
            },
        )

    def _handle_learned_changes(
        *,
        learned_changes: Any,
        batch_id: str,
        source: str,
        mind_transcript_ref: str,
    ) -> list[str]:
        """Apply or record suggested learned changes.

        - When MindSpec.violation_response.auto_learn is true (default), MI will append learned entries.
        - When false, MI will NOT write learned.jsonl; it only records suggestions into EvidenceLog.

        Returns: list of applied learned entry ids (empty if none applied).
        """

        vr = loaded.base.get("violation_response") if isinstance(loaded.base.get("violation_response"), dict) else {}
        auto_learn = bool(vr.get("auto_learn", True))

        # Normalize to a stable, minimal shape (keep severity if present for audit).
        norm: list[dict[str, Any]] = []
        if isinstance(learned_changes, list):
            for ch in learned_changes:
                if not isinstance(ch, dict):
                    continue
                scope = str(ch.get("scope") or "").strip()
                text = str(ch.get("text") or "").strip()
                if scope not in ("global", "project") or not text:
                    continue
                item: dict[str, Any] = {
                    "scope": scope,
                    "text": text,
                    "rationale": str(ch.get("rationale") or "").strip(),
                }
                sev = str(ch.get("severity") or "").strip()
                if sev:
                    item["severity"] = sev
                norm.append(item)

        if not norm:
            return []

        suggestion_id = f"ls_{time.time_ns()}_{secrets.token_hex(4)}"
        applied_entry_ids: list[str] = []

        if auto_learn:
            for item in norm:
                scope = str(item.get("scope") or "").strip()
                text = str(item.get("text") or "").strip()
                rationale = str(item.get("rationale") or "").strip()
                if scope in ("global", "project") and text:
                    base_r = rationale or source
                    r = f"{base_r} (source={source} suggestion={suggestion_id})"
                    applied_entry_ids.append(store.append_learned(project_root=project_path, scope=scope, text=text, rationale=r))

        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "learn_suggested",
                "id": suggestion_id,
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "source": source,
                "auto_learn": auto_learn,
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "learned_changes": norm,
                "applied_entry_ids": applied_entry_ids,
            },
        )

        return applied_entry_ids

    def _log_mind_error(
        *,
        batch_id: str,
        schema_filename: str,
        tag: str,
        error: str,
        mind_transcript_ref: str,
    ) -> None:
        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "mind_error",
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "schema_filename": str(schema_filename),
                "tag": str(tag),
                "mind_transcript_ref": str(mind_transcript_ref or ""),
                "error": _truncate(str(error or ""), 2000),
            },
        )

    def _log_mind_circuit_open(
        *,
        batch_id: str,
        schema_filename: str,
        tag: str,
        error: str,
    ) -> None:
        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "mind_circuit",
                "batch_id": batch_id,
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "state": "open",
                "threshold": mind_circuit_threshold,
                "failures_total": mind_failures_total,
                "failures_consecutive": mind_failures_consecutive,
                "schema_filename": str(schema_filename),
                "tag": str(tag),
                "error": _truncate(str(error or ""), 2000),
            },
        )

    def _mind_call(
        *,
        schema_filename: str,
        prompt: str,
        tag: str,
        batch_id: str,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """Best-effort Mind call wrapper.

        - Never raises (logs to EvidenceLog as kind=mind_error).
        - Circuit breaker: after repeated failures, returns skipped without calling Mind.
        - Returns (obj, mind_transcript_ref, state) where state is ok|error|skipped.
        """

        nonlocal mind_failures_total, mind_failures_consecutive, mind_circuit_open

        if mind_circuit_open:
            return None, "", "skipped"

        try:
            res = llm.call(schema_filename=schema_filename, prompt=prompt, tag=tag)
            obj = getattr(res, "obj", None)
            tp = getattr(res, "transcript_path", None)
            mind_ref = str(tp) if tp else ""
            mind_failures_consecutive = 0
            return (obj if isinstance(obj, dict) else None), mind_ref, "ok"
        except Exception as e:
            mind_ref = ""

            tp = getattr(e, "transcript_path", None)
            if isinstance(tp, Path):
                mind_ref = str(tp)
            elif isinstance(tp, str) and tp.strip():
                mind_ref = tp.strip()
            elif isinstance(e, MindCallError) and e.transcript_path:
                mind_ref = str(e.transcript_path)

            mind_failures_total += 1
            mind_failures_consecutive += 1

            _log_mind_error(
                batch_id=batch_id,
                schema_filename=schema_filename,
                tag=tag,
                error=str(e),
                mind_transcript_ref=mind_ref,
            )
            evidence_window.append(
                {
                    "kind": "mind_error",
                    "batch_id": batch_id,
                    "schema_filename": schema_filename,
                    "tag": tag,
                    "error": _truncate(str(e), 400),
                }
            )
            evidence_window[:] = evidence_window[-8:]

            if not mind_circuit_open and mind_failures_consecutive >= mind_circuit_threshold:
                mind_circuit_open = True
                _log_mind_circuit_open(batch_id=batch_id, schema_filename=schema_filename, tag=tag, error=str(e))
                evidence_window.append(
                    {
                        "kind": "mind_circuit",
                        "batch_id": batch_id,
                        "state": "open",
                        "threshold": mind_circuit_threshold,
                        "failures_consecutive": mind_failures_consecutive,
                        "note": "opened due to repeated mind_error",
                    }
                )
                evidence_window[:] = evidence_window[-8:]

            return None, mind_ref, "error"

    def _segment_add(obj: dict[str, Any]) -> None:
        if not checkpoint_enabled:
            return
        if not isinstance(obj, dict):
            return
        # Keep segment records compact and bounded; they are only used for checkpoint/mine prompts.
        seg: dict[str, Any] = {}
        kind = obj.get("kind")
        if isinstance(kind, str) and kind.strip():
            seg["kind"] = kind.strip()
        bid = obj.get("batch_id")
        if isinstance(bid, str) and bid.strip():
            seg["batch_id"] = bid.strip()

        # Common compact fields.
        for k in ("workflow_id", "workflow_name", "trigger_mode", "trigger_pattern"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                seg[k] = _truncate(v.strip(), 200)

        if seg:
            # Allow small records as-is.
            pass

        # Evidence-like records.
        if obj.get("kind") == "evidence" or ("facts" in obj and "results" in obj and "unknowns" in obj):
            seg["kind"] = "evidence"
            for k in ("facts", "actions", "results", "unknowns", "risk_signals"):
                v = obj.get(k)
                if isinstance(v, list):
                    seg[k] = [str(x)[:300] for x in v[:12] if str(x).strip()]
                else:
                    seg[k] = []
            # A small subset of repo/transcript observations (bounded).
            repo = obj.get("repo_observation") if isinstance(obj.get("repo_observation"), dict) else {}
            if isinstance(repo, dict) and repo:
                seg["repo_observation"] = {
                    "stack_hints": repo.get("stack_hints") if isinstance(repo.get("stack_hints"), list) else [],
                    "has_tests": bool(repo.get("has_tests", False)),
                    "git_is_repo": bool(repo.get("git_is_repo", False)),
                    "git_head": _truncate(str(repo.get("git_head") or ""), 120),
                    "git_diff_stat": _truncate(str(repo.get("git_diff_stat") or ""), 600),
                    "git_diff_cached_stat": _truncate(str(repo.get("git_diff_cached_stat") or ""), 600),
                }
            obs = obj.get("transcript_observation") if isinstance(obj.get("transcript_observation"), dict) else {}
            if isinstance(obs, dict) and obs:
                seg["transcript_observation"] = {
                    "file_paths": (obs.get("file_paths") if isinstance(obs.get("file_paths"), list) else [])[:20],
                    "errors": (obs.get("errors") if isinstance(obs.get("errors"), list) else [])[:10],
                }

        if obj.get("kind") == "risk_event":
            seg["kind"] = "risk_event"
            seg["category"] = _truncate(str(obj.get("category") or ""), 60)
            seg["severity"] = _truncate(str(obj.get("severity") or ""), 60)
            rs = obj.get("risk_signals") if isinstance(obj.get("risk_signals"), list) else []
            seg["risk_signals"] = [str(x)[:200] for x in rs[:8] if str(x).strip()]

        if obj.get("kind") == "check_plan":
            seg["kind"] = "check_plan"
            seg["should_run_checks"] = bool(obj.get("should_run_checks", False))
            seg["needs_testless_strategy"] = bool(obj.get("needs_testless_strategy", False))
            seg["notes"] = _truncate(str(obj.get("notes") or ""), 200)

        if obj.get("kind") == "auto_answer":
            seg["kind"] = "auto_answer"
            seg["should_answer"] = bool(obj.get("should_answer", False))
            seg["needs_user_input"] = bool(obj.get("needs_user_input", False))
            seg["ask_user_question"] = _truncate(str(obj.get("ask_user_question") or ""), 200)

        if obj.get("kind") == "decide_next":
            seg["kind"] = "decide_next"
            seg["next_action"] = _truncate(str(obj.get("next_action") or ""), 40)
            seg["status"] = _truncate(str(obj.get("status") or ""), 40)
            seg["notes"] = _truncate(str(obj.get("notes") or ""), 200)

        if obj.get("kind") == "user_input":
            seg["kind"] = "user_input"
            seg["question"] = _truncate(str(obj.get("question") or ""), 200)
            seg["answer"] = _truncate(str(obj.get("answer") or ""), 200)

        if obj.get("kind") == "cross_project_recall":
            seg["kind"] = "cross_project_recall"
            seg["reason"] = _truncate(str(obj.get("reason") or ""), 60)
            seg["query"] = _truncate(str(obj.get("query") or ""), 200)
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
                        names.append(_truncate(head, 120))
                elif isinstance(it, str) and it.strip():
                    names.append(_truncate(it.strip(), 120))
            if names:
                seg["items"] = names

        if obj.get("kind") == "snapshot":
            seg["kind"] = "snapshot"
            seg["checkpoint_kind"] = _truncate(str(obj.get("checkpoint_kind") or ""), 60)
            seg["status_hint"] = _truncate(str(obj.get("status_hint") or ""), 40)
            tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
            seg["tags"] = [str(x)[:60] for x in tags[:8] if str(x).strip()]

        if seg:
            segment_records.append(seg)
            segment_records[:] = segment_records[-segment_max_records:]

    _last_recall_key = ""

    def _maybe_cross_project_recall(*, batch_id: str, reason: str, query: str) -> None:
        """On-demand cross-project recall (best-effort).

        This writes an EvidenceLog record and appends a compact version to evidence_window so Mind prompts can use it.
        """

        nonlocal _last_recall_key

        if not recall_enabled:
            return
        if reason == "run_start" and not recall_run_start:
            return
        if reason == "before_ask_user" and not recall_before_ask:
            return
        if reason == "risk_signal" and not recall_risk_signal:
            return

        q = str(query or "").strip()
        if not q:
            return

        # Guard: avoid repeated identical recalls in a tight loop.
        key = f"{reason}:{batch_id}:{_truncate(q, 120)}"
        if key == _last_recall_key:
            return
        _last_recall_key = key

        # Ingest small structured stores (workflows/learned) before querying.
        ingest_learned_and_workflows(home_dir=home, index=mem_index)

        items = mem_index.search(
            query=q,
            top_k=recall_top_k,
            kinds=set(recall_kinds),
            include_global=True,
            exclude_project_id=(project_paths.project_id if recall_exclude_current else ""),
        )
        if not items:
            return

        rendered_items, context_text = render_recall_context(items=items, max_chars=recall_max_chars)
        ev = {
            "kind": "cross_project_recall",
            "batch_id": batch_id,
            "ts": now_rfc3339(),
            "thread_id": thread_id or "",
            "reason": reason,
            "query": _truncate(q, 800),
            "top_k": recall_top_k,
            "include_kinds": sorted(recall_kinds),
            "exclude_current_project": bool(recall_exclude_current),
            "items": rendered_items,
            "context_text": context_text,
        }
        append_jsonl(project_paths.evidence_log_path, ev)
        evidence_window.append(
            {
                "kind": "cross_project_recall",
                "batch_id": batch_id,
                "reason": reason,
                "query": _truncate(q, 200),
                "items": rendered_items,
            }
        )
        evidence_window[:] = evidence_window[-8:]
        _segment_add(ev)
        _persist_segment_state()

    # Seed one conservative recall at run start so later Mind calls can use it without bothering the user.
    if recall_enabled and recall_run_start and str(task or "").strip():
        _maybe_cross_project_recall(batch_id="b0.recall", reason="run_start", query=task)

    def _mine_workflow_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        if not bool(wf_auto_mine):
            return
        if executed_batches <= 0:
            return

        auto_enable = bool(wf_cfg.get("auto_enable", True))
        auto_sync = bool(wf_cfg.get("auto_sync_on_change", True))
        allow_single_high = bool(wf_cfg.get("allow_single_if_high_benefit", True))
        try:
            min_occ = int(wf_cfg.get("min_occurrences", 2) or 2)
        except Exception:
            min_occ = 2
        if min_occ < 1:
            min_occ = 1

        mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
        prompt = suggest_workflow_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=loaded.base,
            learned_text=loaded.learned_text,
            project_overlay=loaded.project_overlay,
            recent_evidence=seg_evidence,
            notes=mine_notes,
        )
        out, mind_ref, state = _mind_call(
            schema_filename="suggest_workflow.json",
            prompt=prompt,
            tag=f"suggest_workflow:{base_batch_id}",
            batch_id=f"{base_batch_id}.workflow_suggestion",
        )

        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "workflow_suggestion",
                "batch_id": f"{base_batch_id}.workflow_suggestion",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "state": state,
                "mind_transcript_ref": mind_ref,
                "notes": mine_notes,
                "output": out if isinstance(out, dict) else {},
            },
        )

        if not isinstance(out, dict):
            return
        if not bool(out.get("should_suggest", False)):
            return
        sug = out.get("suggestion")
        if not isinstance(sug, dict):
            return

        signature = str(sug.get("signature") or "").strip()
        if not signature:
            return
        benefit = str(sug.get("benefit") or "").strip()
        reason_s = str(sug.get("reason") or "").strip()
        confidence = sug.get("confidence")
        try:
            conf_f = float(confidence) if confidence is not None else 0.0
        except Exception:
            conf_f = 0.0

        candidates = load_workflow_candidates(project_paths)
        by_sig = candidates.get("by_signature") if isinstance(candidates.get("by_signature"), dict) else {}
        entry = by_sig.get(signature)
        if not isinstance(entry, dict):
            entry = {}

        try:
            prev_n = int(entry.get("count") or 0)
        except Exception:
            prev_n = 0
        already_counted = signature in wf_sigs_counted_in_run
        if already_counted:
            new_n = prev_n
        else:
            new_n = prev_n + 1
            wf_sigs_counted_in_run.add(signature)
        entry["count"] = new_n
        entry["last_ts"] = now_rfc3339()
        entry["benefit"] = benefit
        entry["confidence"] = conf_f
        if reason_s:
            entry["reason"] = reason_s
        wf_obj = sug.get("workflow") if isinstance(sug.get("workflow"), dict) else {}
        name = str(wf_obj.get("name") or "").strip()
        if name:
            entry["workflow_name"] = name

        by_sig[signature] = entry
        candidates["by_signature"] = by_sig
        write_workflow_candidates(project_paths, candidates)

        existing_wid = str(entry.get("workflow_id") or "").strip()
        if existing_wid:
            return

        threshold = min_occ
        if allow_single_high and benefit == "high":
            threshold = 1
        if new_n < threshold:
            return

        if not isinstance(wf_obj, dict) or not wf_obj:
            return

        wid = new_workflow_id()
        wf_final = dict(wf_obj)
        wf_final["id"] = wid
        wf_final["enabled"] = bool(auto_enable)

        src = wf_final.get("source") if isinstance(wf_final.get("source"), dict) else {}
        ev_refs = src.get("evidence_refs") if isinstance(src.get("evidence_refs"), list) else []
        wf_final["source"] = {
            "kind": "suggested",
            "reason": (reason_s or "suggest_workflow") + f" (signature={signature} benefit={benefit} confidence={conf_f:.2f})",
            "evidence_refs": [str(x) for x in ev_refs if str(x).strip()],
        }
        wf_final["created_ts"] = now_rfc3339()
        wf_final["updated_ts"] = now_rfc3339()

        wf_store.write(wf_final)

        entry["workflow_id"] = wid
        entry["solidified_ts"] = now_rfc3339()
        by_sig[signature] = entry
        candidates["by_signature"] = by_sig
        write_workflow_candidates(project_paths, candidates)

        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "workflow_solidified",
                "batch_id": f"{base_batch_id}.workflow_solidified",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "signature": signature,
                "count": new_n,
                "threshold": threshold,
                "benefit": benefit,
                "confidence": conf_f,
                "workflow_id": wid,
                "workflow_name": str(wf_final.get("name") or ""),
                "enabled": bool(wf_final.get("enabled", False)),
            },
        )

        if auto_sync:
            effective = wf_registry.enabled_workflows_effective(overlay=overlay)
            effective = [{k: v for k, v in w.items() if k != "_mi_scope"} for w in effective if isinstance(w, dict)]
            sync_obj = sync_hosts_from_overlay(overlay=overlay, project_id=project_paths.project_id, workflows=effective)
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "host_sync",
                    "batch_id": f"{base_batch_id}.host_sync",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id or "",
                    "source": "workflow_solidified",
                    "sync": sync_obj,
                },
            )

    def _mine_preferences_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        nonlocal loaded, overlay

        if not bool(pref_auto_mine):
            return
        if executed_batches <= 0:
            return

        pref_allow_single_high = bool(pref_cfg.get("allow_single_if_high_benefit", True))
        try:
            pref_min_occ = int(pref_cfg.get("min_occurrences", 2) or 2)
        except Exception:
            pref_min_occ = 2
        if pref_min_occ < 1:
            pref_min_occ = 1
        try:
            pref_min_conf = float(pref_cfg.get("min_confidence", 0.75) or 0.75)
        except Exception:
            pref_min_conf = 0.75
        try:
            pref_max = int(pref_cfg.get("max_suggestions", 3) or 3)
        except Exception:
            pref_max = 3
        if pref_max < 0:
            pref_max = 0
        if pref_max > 10:
            pref_max = 10
        if pref_max == 0:
            return

        mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
        prompt = mine_preferences_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=loaded.base,
            learned_text=loaded.learned_text,
            project_overlay=loaded.project_overlay,
            recent_evidence=seg_evidence,
            notes=mine_notes,
        )
        out, mind_ref, state = _mind_call(
            schema_filename="mine_preferences.json",
            prompt=prompt,
            tag=f"mine_preferences:{base_batch_id}",
            batch_id=f"{base_batch_id}.preference_mining",
        )

        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "preference_mining",
                "batch_id": f"{base_batch_id}.preference_mining",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "state": state,
                "mind_transcript_ref": mind_ref,
                "notes": mine_notes,
                "output": out if isinstance(out, dict) else {},
            },
        )

        if not isinstance(out, dict):
            return
        sugs = out.get("suggestions")
        if not isinstance(sugs, list) or not sugs:
            return

        candidates = load_preference_candidates(project_paths)
        by_sig = candidates.get("by_signature") if isinstance(candidates.get("by_signature"), dict) else {}

        for raw in sugs[:pref_max]:
            if not isinstance(raw, dict):
                continue
            scope = str(raw.get("scope") or "project").strip()
            if scope not in ("global", "project"):
                scope = "project"
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            if loaded.learned_text and text in loaded.learned_text:
                continue

            benefit = str(raw.get("benefit") or "medium").strip()
            if benefit not in ("low", "medium", "high"):
                benefit = "medium"
            rationale = str(raw.get("rationale") or "").strip()
            conf = raw.get("confidence")
            try:
                conf_f = float(conf) if conf is not None else 0.0
            except Exception:
                conf_f = 0.0
            if conf_f < pref_min_conf:
                continue

            sig = preference_signature(scope=scope, text=text)
            entry = by_sig.get(sig)
            if not isinstance(entry, dict):
                entry = {}

            try:
                prev_n = int(entry.get("count") or 0)
            except Exception:
                prev_n = 0
            already_counted = sig in pref_sigs_counted_in_run
            if already_counted:
                new_n = prev_n
            else:
                new_n = prev_n + 1
                pref_sigs_counted_in_run.add(sig)
            entry["count"] = new_n
            entry["last_ts"] = now_rfc3339()
            entry["scope"] = scope
            entry["text"] = text
            entry["benefit"] = benefit
            entry["confidence"] = conf_f
            if rationale:
                entry["rationale"] = rationale

            if bool(entry.get("suggestion_emitted", False)) or bool(entry.get("learned_entry_ids")):
                by_sig[sig] = entry
                continue

            threshold = pref_min_occ
            if pref_allow_single_high and benefit == "high":
                threshold = 1
            if new_n < threshold:
                by_sig[sig] = entry
                continue

            applied_ids = _handle_learned_changes(
                learned_changes=[{"scope": scope, "text": text, "rationale": rationale or "preference_mining", "severity": "medium"}],
                batch_id=f"{base_batch_id}.preference_solidified",
                source="mine_preferences",
                mind_transcript_ref=mind_ref,
            )
            entry["suggestion_emitted"] = True
            entry["suggestion_ts"] = now_rfc3339()
            if applied_ids:
                entry["learned_entry_ids"] = list(applied_ids)
                entry["solidified_ts"] = now_rfc3339()
                loaded = store.load(project_path)
                _refresh_overlay_refs()

            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "preference_solidified",
                    "batch_id": f"{base_batch_id}.preference_solidified",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id or "",
                    "signature": sig,
                    "count": new_n,
                    "threshold": threshold,
                    "benefit": benefit,
                    "confidence": conf_f,
                    "scope": scope,
                    "text": text,
                    "applied_entry_ids": list(applied_ids),
                },
            )
            by_sig[sig] = entry

        candidates["by_signature"] = by_sig
        write_preference_candidates(project_paths, candidates)

    _last_checkpoint_key = ""

    def _maybe_checkpoint_and_mine(*, batch_id: str, planned_next_input: str, status_hint: str, note: str) -> None:
        """LLM-judged checkpoint: may mine workflows/preferences and reset segment buffer."""

        nonlocal segment_state, segment_records, _last_checkpoint_key

        if not checkpoint_enabled:
            return
        if not isinstance(segment_records, list):
            return
        base_bid = str(batch_id or "").split(".", 1)[0].strip()
        if not base_bid:
            return
        # Guard: avoid duplicate checkpoint calls for the same base batch in the same run phase.
        key = base_bid + ":" + str(status_hint or "").strip()
        if key == _last_checkpoint_key:
            return
        _last_checkpoint_key = key

        # Keep thread affinity updated best-effort.
        if isinstance(segment_state, dict):
            cur_tid = str(thread_id or "").strip()
            if cur_tid and cur_tid != "unknown":
                segment_state["thread_id"] = cur_tid
            segment_state["task_hint"] = _truncate(task.strip(), 200)

        prompt = checkpoint_decide_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=loaded.base,
            learned_text=loaded.learned_text,
            project_overlay=loaded.project_overlay,
            segment_evidence=segment_records,
            current_batch_id=base_bid,
            planned_next_input=_truncate(planned_next_input or "", 2000),
            status_hint=str(status_hint or ""),
            notes=(note or "").strip(),
        )
        out, mind_ref, state = _mind_call(
            schema_filename="checkpoint_decide.json",
            prompt=prompt,
            tag=f"checkpoint:{base_bid}",
            batch_id=f"{base_bid}.checkpoint",
        )

        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "checkpoint",
                "batch_id": f"{base_bid}.checkpoint",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "segment_id": str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else "",
                "state": state,
                "mind_transcript_ref": mind_ref,
                "planned_next_input": _truncate(planned_next_input or "", 800),
                "status_hint": str(status_hint or ""),
                "note": (note or "").strip(),
                "output": out if isinstance(out, dict) else {},
            },
        )

        if not isinstance(out, dict):
            _persist_segment_state()
            return

        should_cp = bool(out.get("should_checkpoint", False))
        if not should_cp:
            _persist_segment_state()
            return

        # Mine before resetting segment.
        if bool(out.get("should_mine_workflow", False)):
            _mine_workflow_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")
        if bool(out.get("should_mine_preferences", False)):
            _mine_preferences_from_segment(seg_evidence=segment_records, base_batch_id=base_bid, source="checkpoint")

        # Materialize a compact snapshot for cross-project recall (append-only; traceable to segment records).
        try:
            seg_id = str(segment_state.get("segment_id") or "") if isinstance(segment_state, dict) else ""
            task_hint = str(segment_state.get("task_hint") or task) if isinstance(segment_state, dict) else task
            snap_ev, snap_item = build_snapshot_item(
                project_id=project_paths.project_id,
                segment_id=seg_id,
                thread_id=str(thread_id or ""),
                batch_id=f"{base_bid}.snapshot",
                task_hint=task_hint,
                checkpoint_kind=str(out.get("checkpoint_kind") or ""),
                status_hint=str(status_hint or ""),
                checkpoint_notes=str(out.get("notes") or ""),
                segment_records=segment_records,
            )
            append_jsonl(project_paths.evidence_log_path, snap_ev)
            evidence_window.append(
                {
                    "kind": "snapshot",
                    "batch_id": snap_ev.get("batch_id"),
                    "checkpoint_kind": snap_ev.get("checkpoint_kind"),
                    "status_hint": snap_ev.get("status_hint"),
                    "tags": snap_ev.get("tags"),
                    "text": _truncate(str(snap_ev.get("text") or ""), 600),
                }
            )
            evidence_window[:] = evidence_window[-8:]
            mem_index.upsert_items([snap_item])
        except Exception:
            pass

        # Reset segment buffer for the next phase.
        segment_state = _new_segment_state(reason=f"checkpoint:{out.get('checkpoint_kind')}", thread_hint=str(thread_id or ""))
        segment_records = segment_state.get("records") if isinstance(segment_state.get("records"), list) else []
        segment_state["records"] = segment_records
        _persist_segment_state()

    def _queue_next_input(*, nxt: str, codex_last_message: str, batch_id: str, reason: str) -> bool:
        """Set next_input for the next Hands batch, with loop-guard and optional user intervention."""
        nonlocal next_input, status, notes, sent_sigs

        candidate = (nxt or "").strip()
        if not candidate:
            status = "blocked"
            notes = f"{reason}: empty next input"
            return False

        sig = _loop_sig(codex_last_message=codex_last_message, next_input=candidate)
        sent_sigs.append(sig)
        sent_sigs = sent_sigs[-6:]

        pattern = _loop_pattern(sent_sigs)
        if pattern:
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "loop_guard",
                    "batch_id": batch_id,
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "pattern": pattern,
                    "codex_last_message": _truncate(codex_last_message, 800),
                    "next_input": _truncate(candidate, 800),
                    "reason": reason,
                },
            )
            evidence_window.append({"kind": "loop_guard", "batch_id": batch_id, "pattern": pattern, "reason": reason})
            evidence_window[:] = evidence_window[-8:]

            ask_when_uncertain = bool((loaded.base.get("defaults") or {}).get("ask_when_uncertain", True))
            if ask_when_uncertain:
                q = (
                    "MI detected a repeated loop (pattern="
                    + pattern
                    + "). Provide a new instruction to send to Hands, or type 'stop' to end:"
                )
                override = _read_user_answer(q)
                append_jsonl(
                    project_paths.evidence_log_path,
                    {
                        "kind": "user_input",
                        "batch_id": batch_id,
                        "ts": now_rfc3339(),
                        "thread_id": thread_id,
                        "question": q,
                        "answer": override,
                    },
                )
                evidence_window.append({"kind": "user_input", "batch_id": batch_id, "question": q, "answer": override})
                evidence_window[:] = evidence_window[-8:]
                _segment_add({"kind": "user_input", "batch_id": batch_id, "question": q, "answer": override})
                _persist_segment_state()

                ov = override.strip()
                if not ov or ov.lower() in ("stop", "quit", "q"):
                    status = "blocked"
                    notes = "stopped by loop_guard"
                    return False
                candidate = ov
                sent_sigs.clear()
            else:
                status = "blocked"
                notes = "loop_guard triggered"
                return False

        # Checkpoint after the current batch, before sending the next instruction to Hands.
        _maybe_checkpoint_and_mine(
            batch_id=batch_id,
            planned_next_input=candidate,
            status_hint="not_done",
            note="before_continue: " + reason,
        )

        next_input = candidate
        status = "not_done"
        notes = reason
        return True

    executed_batches = 0
    last_batch_id = ""
    max_batches_exhausted = False
    for batch_idx in range(max_batches):
        batch_id = f"b{batch_idx}"
        last_batch_id = batch_id
        batch_ts = now_rfc3339().replace(":", "").replace("-", "")
        hands_transcript = project_paths.transcripts_dir / "hands" / f"{batch_ts}_b{batch_idx}.jsonl"

        light = loaded.light_injection()
        batch_input = next_input.strip()
        codex_prompt = light + "\n" + batch_input + "\n"
        sent_ts = now_rfc3339()
        prompt_sha256 = hashlib.sha256(codex_prompt.encode("utf-8")).hexdigest()

        use_resume = thread_id is not None and hands_resume is not None and thread_id != "unknown"
        attempted_overlay_resume = bool(use_resume and resumed_from_overlay and batch_idx == 0)

        if not use_resume:
            result = hands_exec(
                prompt=codex_prompt,
                project_root=project_path,
                transcript_path=hands_transcript,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=interrupt_cfg,
            )
        else:
            result = hands_resume(
                thread_id=thread_id,
                prompt=codex_prompt,
                project_root=project_path,
                transcript_path=hands_transcript,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=interrupt_cfg,
            )

            # If we resumed using a persisted thread id and it failed, fall back to a fresh exec.
            if attempted_overlay_resume and int(getattr(result, "exit_code", 0) or 0) != 0:
                append_jsonl(
                    project_paths.evidence_log_path,
                    {
                        "kind": "hands_resume_failed",
                        "batch_id": batch_id,
                        "ts": now_rfc3339(),
                        "thread_id": thread_id,
                        "provider": cur_provider,
                        "exit_code": getattr(result, "exit_code", None),
                        "notes": "resume failed; falling back to exec",
                        "transcript_path": str(hands_transcript),
                    },
                )
                hands_transcript = project_paths.transcripts_dir / "hands" / f"{batch_ts}_b{batch_idx}_exec_after_resume_fail.jsonl"
                result = hands_exec(
                    prompt=codex_prompt,
                    project_root=project_path,
                    transcript_path=hands_transcript,
                    full_auto=True,
                    sandbox=None,
                    output_schema_path=None,
                    interrupt=interrupt_cfg,
                )

        # Prefer a non-unknown thread id when available.
        res_tid = str(getattr(result, "thread_id", "") or "")
        if res_tid and res_tid != "unknown":
            thread_id = res_tid
        elif thread_id is None:
            thread_id = res_tid or "unknown"

        executed_batches += 1

        # Persist last seen Hands thread id so future `mi run --continue-hands` can resume.
        if thread_id and thread_id != "unknown":
            hands_state = overlay.get("hands_state") if isinstance(overlay.get("hands_state"), dict) else {}
            if not isinstance(hands_state, dict):
                hands_state = {}
                overlay["hands_state"] = hands_state
            if cur_provider:
                hands_state["provider"] = cur_provider
            if str(hands_state.get("thread_id") or "") != thread_id or not str(hands_state.get("updated_ts") or ""):
                hands_state["thread_id"] = thread_id
                hands_state["updated_ts"] = now_rfc3339()
                store.write_project_overlay(project_path, overlay)

        # Persist exactly what MI sent to Hands (transparency + later audit).
        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "hands_input",
                "batch_id": batch_id,
                "ts": sent_ts,
                "thread_id": thread_id or result.thread_id,
                "transcript_path": str(hands_transcript),
                "input": batch_input,
                "light_injection": light,
                "prompt_sha256": prompt_sha256,
            },
        )

        repo_obs = _observe_repo(project_path)

        # Evidence extraction (LLM) from machine summary.
        summary = _batch_summary(result)
        extract_prompt = extract_evidence_prompt(
            task=task,
            hands_provider=cur_provider,
            light_injection=light,
            batch_input=batch_input,
            codex_batch_summary=summary,
            repo_observation=repo_obs,
        )
        evidence_obj, evidence_mind_ref, evidence_state = _mind_call(
            schema_filename="extract_evidence.json",
            prompt=extract_prompt,
            tag=f"extract_b{batch_idx}",
            batch_id=batch_id,
        )
        if evidence_obj is None:
            if evidence_state == "skipped":
                evidence_obj = _empty_evidence_obj(note="mind_circuit_open: extract_evidence skipped")
            else:
                evidence_obj = _empty_evidence_obj(note="mind_error: extract_evidence failed; see EvidenceLog kind=mind_error")
        evidence_item = {
            "batch_id": batch_id,
            "ts": now_rfc3339(),
            "thread_id": thread_id,
            "hands_transcript_ref": str(hands_transcript),
            "codex_transcript_ref": str(hands_transcript),  # legacy key (V1 early logs)
            "mind_transcript_ref": evidence_mind_ref,
            "mi_input": batch_input,
            "transcript_observation": summary.get("transcript_observation") or {},
            "repo_observation": repo_obs,
            **evidence_obj,
        }
        append_jsonl(project_paths.evidence_log_path, evidence_item)
        evidence_window.append(evidence_item)
        evidence_window = evidence_window[-8:]
        _segment_add(evidence_item)
        _persist_segment_state()

        # Best-effort workflow progress: infer which workflow steps are completed and what the next step is.
        active_wf = _active_workflow()
        if isinstance(active_wf, dict) and active_wf:
            latest_evidence = {
                "batch_id": batch_id,
                "facts": evidence_obj.get("facts") if isinstance(evidence_obj, dict) else [],
                "actions": evidence_obj.get("actions") if isinstance(evidence_obj, dict) else [],
                "results": evidence_obj.get("results") if isinstance(evidence_obj, dict) else [],
                "unknowns": evidence_obj.get("unknowns") if isinstance(evidence_obj, dict) else [],
                "risk_signals": evidence_obj.get("risk_signals") if isinstance(evidence_obj, dict) else [],
                "repo_observation": repo_obs,
                "transcript_observation": summary.get("transcript_observation") or {},
            }
            wf_prog_prompt = workflow_progress_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                workflow=active_wf,
                workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
                latest_evidence=latest_evidence,
                last_batch_input=batch_input,
                codex_last_message=result.last_agent_message(),
            )
            wf_prog_obj, wf_prog_ref, wf_prog_state = _mind_call(
                schema_filename="workflow_progress.json",
                prompt=wf_prog_prompt,
                tag=f"wf_progress_b{batch_idx}",
                batch_id=f"{batch_id}.workflow_progress",
            )
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "workflow_progress",
                    "batch_id": f"{batch_id}.workflow_progress",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "workflow_id": str(active_wf.get("id") or ""),
                    "workflow_name": str(active_wf.get("name") or ""),
                    "state": wf_prog_state,
                    "mind_transcript_ref": wf_prog_ref,
                    "output": wf_prog_obj if isinstance(wf_prog_obj, dict) else {},
                },
            )

            if isinstance(wf_prog_obj, dict) and bool(wf_prog_obj.get("should_update", False)):
                step_allow = set(_workflow_step_ids(active_wf))
                raw_done = wf_prog_obj.get("completed_step_ids") if isinstance(wf_prog_obj.get("completed_step_ids"), list) else []
                done_ids: list[str] = []
                seen_done: set[str] = set()
                for x in raw_done:
                    xs = str(x or "").strip()
                    if not xs or xs in seen_done:
                        continue
                    if xs not in step_allow:
                        continue
                    seen_done.add(xs)
                    done_ids.append(xs)

                nxt = str(wf_prog_obj.get("next_step_id") or "").strip()
                if nxt and nxt not in step_allow:
                    nxt = ""
                if not nxt:
                    # Deterministic fallback: first step not marked done (list order).
                    for sid in _workflow_step_ids(active_wf):
                        if sid not in seen_done:
                            nxt = sid
                            break

                workflow_run["version"] = str(workflow_run.get("version") or "v1")
                workflow_run["active"] = bool(workflow_run.get("active", True))
                workflow_run["workflow_id"] = str(active_wf.get("id") or workflow_run.get("workflow_id") or "")
                workflow_run["workflow_name"] = str(active_wf.get("name") or workflow_run.get("workflow_name") or "")
                if thread_id and thread_id != "unknown":
                    workflow_run["thread_id"] = thread_id
                workflow_run["completed_step_ids"] = done_ids
                workflow_run["next_step_id"] = nxt
                workflow_run["last_batch_id"] = batch_id
                workflow_run["last_confidence"] = wf_prog_obj.get("confidence")
                workflow_run["last_notes"] = str(wf_prog_obj.get("notes") or "").strip()
                workflow_run["updated_ts"] = now_rfc3339()

                should_close = bool(wf_prog_obj.get("should_close", False))
                if should_close or not nxt:
                    workflow_run["active"] = False
                    workflow_run["close_reason"] = str(wf_prog_obj.get("close_reason") or "").strip()

                overlay["workflow_run"] = workflow_run
                store.write_project_overlay(project_path, overlay)

        # Post-hoc risk judgement (LLM) when heuristic signals are present.
        risk_signals = _detect_risk_signals(result)
        if not risk_signals and not (isinstance(getattr(result, "events", None), list) and result.events):
            risk_signals = _detect_risk_signals_from_transcript(hands_transcript)
        if risk_signals:
            # On-demand recall: similar past risk decisions/preferences can reduce unnecessary prompting.
            _maybe_cross_project_recall(
                batch_id=f"{batch_id}.risk_recall",
                reason="risk_signal",
                query=(" ".join([str(x) for x in risk_signals if str(x).strip()]) + "\n" + task).strip(),
            )
            risk_prompt = risk_judge_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                risk_signals=risk_signals,
                codex_last_message=result.last_agent_message(),
            )
            risk_obj, risk_mind_ref, risk_state = _mind_call(
                schema_filename="risk_judge.json",
                prompt=risk_prompt,
                tag=f"risk_b{batch_idx}",
                batch_id=batch_id,
            )
            if risk_obj is None:
                # Conservative fallback when Mind cannot evaluate a risky signal.
                cat = "other"
                sev = "high"
                for s in risk_signals:
                    prefix = s.split(":", 1)[0].strip().lower()
                    if prefix in ("network", "install", "push", "publish", "delete", "privilege"):
                        cat = prefix
                        break
                if cat == "delete":
                    sev = "critical"
                risk_obj = {
                    "category": cat,
                    "severity": sev,
                    "should_ask_user": True,
                    "mitigation": [
                        ("mind_circuit_open: risk_judge skipped; treat as high risk" if risk_state == "skipped" else "mind_error: risk_judge failed; treat as high risk")
                    ],
                    "learned_changes": [],
                }
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "risk_event",
                    "batch_id": f"b{batch_idx}",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "risk_signals": risk_signals,
                    "mind_transcript_ref": risk_mind_ref,
                    "risk": risk_obj,
                },
            )
            evidence_window.append({"kind": "risk_event", "batch_id": f"b{batch_idx}", **risk_obj})
            evidence_window = evidence_window[-8:]
            _segment_add(
                {
                    "kind": "risk_event",
                    "batch_id": f"b{batch_idx}",
                    "risk_signals": risk_signals,
                    "category": risk_obj.get("category"),
                    "severity": risk_obj.get("severity"),
                }
            )
            _persist_segment_state()

            # Learned tightening suggestions from risk_judge.
            applied = _handle_learned_changes(
                learned_changes=risk_obj.get("learned_changes"),
                batch_id=f"b{batch_idx}",
                source="risk_judge",
                mind_transcript_ref=risk_mind_ref,
            )
            if applied:
                loaded = store.load(project_path)
                _refresh_overlay_refs()

            # Optional immediate user escalation on high risk.
            vr = loaded.base.get("violation_response") or {}
            prompt_user = bool(vr.get("prompt_user_on_high_risk", True))
            severity = str(risk_obj.get("severity") or "low")
            should_ask_user = bool(risk_obj.get("should_ask_user", False))
            cat = str(risk_obj.get("category") or "other")

            sev_list = vr.get("prompt_user_risk_severities")
            if isinstance(sev_list, list) and any(str(x).strip() for x in sev_list):
                sev_allow = {str(x).strip() for x in sev_list if str(x).strip()}
            else:
                sev_allow = {"high", "critical"}

            cat_list = vr.get("prompt_user_risk_categories")
            if isinstance(cat_list, list) and any(str(x).strip() for x in cat_list):
                cat_allow = {str(x).strip() for x in cat_list if str(x).strip()}
            else:
                cat_allow = set()

            respect_should = bool(vr.get("prompt_user_respect_should_ask_user", True))
            should_prompt = (
                prompt_user
                and (severity in sev_allow)
                and (not cat_allow or cat in cat_allow)
                and (should_ask_user if respect_should else True)
            )

            if should_prompt:
                mitig = risk_obj.get("mitigation") or []
                mitig_s = "; ".join([str(x) for x in mitig if str(x).strip()][:3])
                q = f"Risk action detected (category={cat}, severity={severity}). Continue? (y/N)\nMitigation: {mitig_s}"
                answer = _read_user_answer(q)
                if answer.strip().lower() not in ("y", "yes"):
                    status = "blocked"
                    notes = "stopped after risk event"
                    break

        repo_obs = evidence_item.get("repo_observation") or {}
        codex_last = result.last_agent_message()

        # Plan minimal checks (LLM) only when uncertainty/risk/change suggests it.
        checks_obj = _empty_check_plan()
        if _should_plan_checks(
            summary=summary,
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            codex_last_message=codex_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        ):
            checks_prompt = plan_min_checks_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                recent_evidence=evidence_window,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            )
            checks_obj, checks_mind_ref, checks_state = _mind_call(
                schema_filename="plan_min_checks.json",
                prompt=checks_prompt,
                tag=f"checks_b{batch_idx}",
                batch_id=batch_id,
            )
            if checks_obj is None:
                checks_obj = _empty_check_plan()
                if checks_state == "skipped":
                    checks_obj["notes"] = "skipped: mind_circuit_open (plan_min_checks)"
                else:
                    checks_obj["notes"] = "mind_error: plan_min_checks failed; see EvidenceLog kind=mind_error"
        else:
            checks_obj = _empty_check_plan()
            checks_obj["notes"] = "skipped: no uncertainty/risk/question detected"
            checks_mind_ref = ""
        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "check_plan",
                "batch_id": f"b{batch_idx}",
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "mind_transcript_ref": checks_mind_ref,
                "checks": checks_obj,
            },
        )
        evidence_window.append({"kind": "check_plan", "batch_id": f"b{batch_idx}", **checks_obj})
        evidence_window = evidence_window[-8:]
        _segment_add({"kind": "check_plan", "batch_id": f"b{batch_idx}", **(checks_obj if isinstance(checks_obj, dict) else {})})
        _persist_segment_state()

        # Auto-answer Hands when it is asking the user questions; only ask the user if MI cannot answer.
        auto_answer_obj = _empty_auto_answer()
        if _looks_like_user_question(codex_last):
            aa_prompt = auto_answer_to_codex_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                recent_evidence=evidence_window,
                codex_last_message=codex_last,
            )
            auto_answer_mind_ref = ""
            aa_obj, auto_answer_mind_ref, aa_state = _mind_call(
                schema_filename="auto_answer_to_codex.json",
                prompt=aa_prompt,
                tag=f"autoanswer_b{batch_idx}",
                batch_id=batch_id,
            )
            if aa_obj is None:
                auto_answer_obj = _empty_auto_answer()
                if aa_state == "skipped":
                    auto_answer_obj["notes"] = "skipped: mind_circuit_open (auto_answer_to_codex)"
                else:
                    auto_answer_obj["notes"] = "mind_error: auto_answer_to_codex failed; see EvidenceLog kind=mind_error"
            else:
                auto_answer_obj = aa_obj
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "auto_answer",
                    "batch_id": f"b{batch_idx}",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "mind_transcript_ref": auto_answer_mind_ref,
                    "auto_answer": auto_answer_obj,
                },
            )
            evidence_window.append({"kind": "auto_answer", "batch_id": f"b{batch_idx}", **auto_answer_obj})
            evidence_window = evidence_window[-8:]
            _segment_add({"kind": "auto_answer", "batch_id": f"b{batch_idx}", **(auto_answer_obj if isinstance(auto_answer_obj, dict) else {})})
            _persist_segment_state()

        # Deterministic pre-action arbitration to minimize user burden:
        # 1) If auto_answer requires user input -> ask user, then send answer to Hands (optionally with checks).
        # 2) If minimal checks require a testless verification strategy and it hasn't been chosen -> ask once and persist.
        # 3) If MI can answer Hands and/or run minimal checks -> send to Hands (skip decide_next for this iteration).
        if isinstance(auto_answer_obj, dict) and bool(auto_answer_obj.get("needs_user_input", False)):
            q = str(auto_answer_obj.get("ask_user_question") or "").strip() or codex_last.strip() or "Need more information:"
            # Before asking the user, do a conservative cross-project recall and retry auto_answer once.
            _maybe_cross_project_recall(
                batch_id=f"b{batch_idx}.before_user_recall",
                reason="before_ask_user",
                query=(q + "\n" + task).strip(),
            )
            aa_retry = _empty_auto_answer()
            aa_prompt_retry = auto_answer_to_codex_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                recent_evidence=evidence_window,
                codex_last_message=q,
            )
            aa_obj_r, aa_r_ref, aa_r_state = _mind_call(
                schema_filename="auto_answer_to_codex.json",
                prompt=aa_prompt_retry,
                tag=f"autoanswer_retry_after_recall_b{batch_idx}",
                batch_id=f"b{batch_idx}.after_recall",
            )
            if aa_obj_r is None:
                aa_retry = _empty_auto_answer()
                if aa_r_state == "skipped":
                    aa_retry["notes"] = "skipped: mind_circuit_open (auto_answer_to_codex retry after recall)"
                else:
                    aa_retry["notes"] = "mind_error: auto_answer_to_codex retry failed; see EvidenceLog kind=mind_error"
            else:
                aa_retry = aa_obj_r
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "auto_answer",
                    "batch_id": f"b{batch_idx}.after_recall",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "mind_transcript_ref": aa_r_ref,
                    "auto_answer": aa_retry,
                },
            )
            evidence_window.append({"kind": "auto_answer", "batch_id": f"b{batch_idx}.after_recall", **aa_retry})
            evidence_window[:] = evidence_window[-8:]
            _segment_add({"kind": "auto_answer", "batch_id": f"b{batch_idx}.after_recall", **(aa_retry if isinstance(aa_retry, dict) else {})})
            _persist_segment_state()

            aa_text2 = ""
            if isinstance(aa_retry, dict) and bool(aa_retry.get("should_answer", False)):
                aa_text2 = str(aa_retry.get("codex_answer_input") or "").strip()
            if aa_text2:
                check_text2 = ""
                if isinstance(checks_obj, dict) and bool(checks_obj.get("should_run_checks", False)):
                    check_text2 = str(checks_obj.get("codex_check_input") or "").strip()
                combined2 = "\n\n".join([x for x in [aa_text2, check_text2] if x])
                if combined2:
                    if not _queue_next_input(
                        nxt=combined2,
                        codex_last_message=codex_last,
                        batch_id=f"b{batch_idx}.after_recall",
                        reason="auto-answered after cross-project recall",
                    ):
                        break
                    continue

            if isinstance(aa_retry, dict) and bool(aa_retry.get("needs_user_input", False)):
                q2 = str(aa_retry.get("ask_user_question") or "").strip()
                if q2:
                    q = q2

            answer = _read_user_answer(q)
            if not answer:
                status = "blocked"
                notes = "user did not provide required input"
                break
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "user_input",
                    "batch_id": f"b{batch_idx}",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "question": q,
                    "answer": answer,
                },
            )
            evidence_window.append({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": answer})
            evidence_window = evidence_window[-8:]
            _segment_add({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": answer})
            _persist_segment_state()

            check_text = ""
            if isinstance(checks_obj, dict) and bool(checks_obj.get("should_run_checks", False)):
                check_text = str(checks_obj.get("codex_check_input") or "").strip()
            combined_user = "\n\n".join([x for x in [answer.strip(), check_text] if x])
            if not _queue_next_input(
                nxt=combined_user,
                codex_last_message=codex_last,
                batch_id=f"b{batch_idx}",
                reason="answered after user input",
            ):
                break
            continue

        tls = loaded.project_overlay.get("testless_verification_strategy") if isinstance(loaded.project_overlay, dict) else None
        tls_chosen_once = bool(tls.get("chosen_once", False)) if isinstance(tls, dict) else False
        needs_tls = bool(checks_obj.get("needs_testless_strategy", False)) if isinstance(checks_obj, dict) else False
        if needs_tls and not tls_chosen_once:
            q = str(checks_obj.get("testless_strategy_question") or "").strip()
            if not q:
                q = "This project appears to have no tests. What testless verification strategy should MI use for this project (one-time)?"
            answer = _read_user_answer(q)
            if not answer:
                status = "blocked"
                notes = "user did not provide required input"
                break
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "user_input",
                    "batch_id": f"b{batch_idx}",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "question": q,
                    "answer": answer,
                },
            )
            evidence_window.append({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": answer})
            evidence_window = evidence_window[-8:]
            _segment_add({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": answer})
            _persist_segment_state()

            loaded.project_overlay.setdefault("testless_verification_strategy", {})
            loaded.project_overlay["testless_verification_strategy"] = {
                "chosen_once": True,
                "strategy": answer.strip(),
                "rationale": "user provided testless verification strategy",
            }
            store.write_project_overlay(project_path, loaded.project_overlay)
            loaded = store.load(project_path)
            _refresh_overlay_refs()

            # Re-plan checks now that the project has a strategy to follow.
            checks_prompt2 = plan_min_checks_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                recent_evidence=evidence_window,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            )
            checks_obj2, checks_mind_ref2, checks_state2 = _mind_call(
                schema_filename="plan_min_checks.json",
                prompt=checks_prompt2,
                tag=f"checks_after_tls_b{batch_idx}",
                batch_id=f"b{batch_idx}.after_testless",
            )
            if checks_obj2 is None:
                checks_obj2 = _empty_check_plan()
                if checks_state2 == "skipped":
                    checks_obj2["notes"] = "skipped: mind_circuit_open (plan_min_checks after_testless)"
                else:
                    checks_obj2["notes"] = "mind_error: plan_min_checks(after_testless) failed; see EvidenceLog kind=mind_error"
            checks_obj = checks_obj2
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "check_plan",
                    "batch_id": f"b{batch_idx}.after_testless",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "mind_transcript_ref": checks_mind_ref2,
                    "checks": checks_obj,
                },
            )
            evidence_window.append({"kind": "check_plan", "batch_id": f"b{batch_idx}.after_testless", **checks_obj})
            evidence_window = evidence_window[-8:]
            _segment_add({"kind": "check_plan", "batch_id": f"b{batch_idx}.after_testless", **(checks_obj if isinstance(checks_obj, dict) else {})})
            _persist_segment_state()

        answer_text = ""
        if isinstance(auto_answer_obj, dict) and bool(auto_answer_obj.get("should_answer", False)):
            answer_text = str(auto_answer_obj.get("codex_answer_input") or "").strip()
        check_text = ""
        if isinstance(checks_obj, dict) and bool(checks_obj.get("should_run_checks", False)):
            check_text = str(checks_obj.get("codex_check_input") or "").strip()
        combined = "\n\n".join([x for x in [answer_text, check_text] if x])
        if combined:
            if not _queue_next_input(
                nxt=combined,
                codex_last_message=codex_last,
                batch_id=f"b{batch_idx}",
                reason="sent auto-answer/checks to Hands",
            ):
                break
            continue

        # Decide what to do next.
        decision_prompt = decide_next_prompt(
            task=task,
            hands_provider=cur_provider,
            mindspec_base=loaded.base,
            learned_text=loaded.learned_text,
            project_overlay=loaded.project_overlay,
            active_workflow=_active_workflow(),
            workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
            recent_evidence=evidence_window,
            codex_last_message=codex_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer=auto_answer_obj,
        )
        decision_obj, decision_mind_ref, decision_state = _mind_call(
            schema_filename="decide_next.json",
            prompt=decision_prompt,
            tag=f"decide_b{batch_idx}",
            batch_id=batch_id,
        )
        if decision_obj is None:
            ask_when_uncertain = bool((loaded.base.get("defaults") or {}).get("ask_when_uncertain", True))
            if ask_when_uncertain:
                if decision_state == "skipped":
                    if _looks_like_user_question(codex_last):
                        q = codex_last.strip()
                    else:
                        q = (
                            "MI Mind circuit is OPEN (repeated failures). "
                            "Provide the next instruction to send to Hands, or type 'stop' to end:"
                        )
                else:
                    q = "MI Mind failed to decide next action. Provide next instruction to send to Hands, or type 'stop' to end:"
                override = _read_user_answer(q)
                append_jsonl(
                    project_paths.evidence_log_path,
                    {
                        "kind": "user_input",
                        "batch_id": f"b{batch_idx}",
                        "ts": now_rfc3339(),
                        "thread_id": thread_id,
                        "question": q,
                        "answer": override,
                    },
                )
                evidence_window.append({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": override})
                evidence_window = evidence_window[-8:]
                _segment_add({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": override})
                _persist_segment_state()

                ov = (override or "").strip()
                if not ov or ov.lower() in ("stop", "quit", "q"):
                    status = "blocked"
                    notes = "stopped after mind_circuit_open(decide_next)" if decision_state == "skipped" else "stopped after mind_error(decide_next)"
                    break
                if not _queue_next_input(
                    nxt=ov,
                    codex_last_message=codex_last,
                    batch_id=f"b{batch_idx}",
                    reason="mind_circuit_open(decide_next): user override" if decision_state == "skipped" else "mind_error(decide_next): user override",
                ):
                    break
                continue

            status = "blocked"
            notes = (
                "mind_circuit_open(decide_next): could not proceed (ask_when_uncertain=false)"
                if decision_state == "skipped"
                else "mind_error(decide_next): could not proceed (ask_when_uncertain=false)"
            )
            break

        _log_decide_next(
            decision_obj=decision_obj,
            batch_id=f"b{batch_idx}",
            phase="initial",
            mind_transcript_ref=decision_mind_ref,
        )
        _segment_add(
            {
                "kind": "decide_next",
                "batch_id": f"b{batch_idx}",
                "next_action": decision_obj.get("next_action"),
                "status": decision_obj.get("status"),
                "notes": decision_obj.get("notes"),
            }
        )
        _persist_segment_state()

        # Apply project overlay updates (e.g., testless verification strategy).
        overlay_update = decision_obj.get("update_project_overlay") or {}
        if isinstance(overlay_update, dict):
            set_tls = overlay_update.get("set_testless_strategy")
            if isinstance(set_tls, dict):
                strategy = str(set_tls.get("strategy") or "").strip()
                rationale = str(set_tls.get("rationale") or "").strip()
                if strategy:
                    loaded.project_overlay.setdefault("testless_verification_strategy", {})
                    loaded.project_overlay["testless_verification_strategy"] = {
                        "chosen_once": True,
                        "strategy": strategy,
                        "rationale": rationale,
                    }
                    store.write_project_overlay(project_path, loaded.project_overlay)

        # Write learned changes (append-only; reversible via future tooling).
        applied = _handle_learned_changes(
            learned_changes=decision_obj.get("learned_changes"),
            batch_id=f"b{batch_idx}",
            source="decide_next",
            mind_transcript_ref=decision_mind_ref,
        )

        # Refresh loaded learned text after any new writes.
        if applied:
            loaded = store.load(project_path)
            _refresh_overlay_refs()

        next_action = str(decision_obj.get("next_action") or "stop")
        status = str(decision_obj.get("status") or "not_done")
        notes = str(decision_obj.get("notes") or "")

        if next_action == "stop":
            break

        if next_action == "ask_user":
            q = str(decision_obj.get("ask_user_question") or "Need more information:").strip()

            # Before bothering the user, attempt to auto-answer using values/evidence.
            aa_from_decide = _empty_auto_answer()
            if q:
                aa_prompt2 = auto_answer_to_codex_prompt(
                    task=task,
                    hands_provider=cur_provider,
                    mindspec_base=loaded.base,
                    learned_text=loaded.learned_text,
                    project_overlay=loaded.project_overlay,
                    repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                    check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                    recent_evidence=evidence_window,
                    codex_last_message=q,
                )
                aa_obj2, aa2_mind_ref, aa2_state = _mind_call(
                    schema_filename="auto_answer_to_codex.json",
                    prompt=aa_prompt2,
                    tag=f"autoanswer_from_decide_b{batch_idx}",
                    batch_id=f"b{batch_idx}.from_decide",
                )
                if aa_obj2 is None:
                    aa_from_decide = _empty_auto_answer()
                    if aa2_state == "skipped":
                        aa_from_decide["notes"] = "skipped: mind_circuit_open (auto_answer_to_codex from decide_next)"
                    else:
                        aa_from_decide["notes"] = "mind_error: auto_answer_to_codex(from decide_next) failed; see EvidenceLog kind=mind_error"
                else:
                    aa_from_decide = aa_obj2

                append_jsonl(
                    project_paths.evidence_log_path,
                    {
                        "kind": "auto_answer",
                        "batch_id": f"b{batch_idx}.from_decide",
                        "ts": now_rfc3339(),
                        "thread_id": thread_id,
                        "mind_transcript_ref": aa2_mind_ref,
                        "auto_answer": aa_from_decide,
                    },
                )
                evidence_window.append({"kind": "auto_answer", "batch_id": f"b{batch_idx}.from_decide", **aa_from_decide})
                evidence_window = evidence_window[-8:]
                _segment_add({"kind": "auto_answer", "batch_id": f"b{batch_idx}.from_decide", **(aa_from_decide if isinstance(aa_from_decide, dict) else {})})
                _persist_segment_state()

                aa_text = ""
                if isinstance(aa_from_decide, dict) and bool(aa_from_decide.get("should_answer", False)):
                    aa_text = str(aa_from_decide.get("codex_answer_input") or "").strip()
                chk_text = ""
                if isinstance(checks_obj, dict) and bool(checks_obj.get("should_run_checks", False)):
                    chk_text = str(checks_obj.get("codex_check_input") or "").strip()
                combined2 = "\n\n".join([x for x in [aa_text, chk_text] if x])
                if combined2:
                    if not _queue_next_input(
                        nxt=combined2,
                        codex_last_message=codex_last,
                        batch_id=f"b{batch_idx}.from_decide",
                        reason="auto-answered instead of prompting user",
                    ):
                        break
                    continue

                if isinstance(aa_from_decide, dict) and bool(aa_from_decide.get("needs_user_input", False)):
                    q2 = str(aa_from_decide.get("ask_user_question") or "").strip()
                    if q2:
                        q = q2

            # Before asking the user, do a conservative cross-project recall and retry auto_answer once.
            _maybe_cross_project_recall(
                batch_id=f"b{batch_idx}.from_decide.before_user_recall",
                reason="before_ask_user",
                query=(q + "\n" + task).strip(),
            )
            aa_retry2 = _empty_auto_answer()
            if q:
                aa_prompt3 = auto_answer_to_codex_prompt(
                    task=task,
                    hands_provider=cur_provider,
                    mindspec_base=loaded.base,
                    learned_text=loaded.learned_text,
                    project_overlay=loaded.project_overlay,
                    repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                    check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                    recent_evidence=evidence_window,
                    codex_last_message=q,
                )
                aa_obj3, aa3_ref, aa3_state = _mind_call(
                    schema_filename="auto_answer_to_codex.json",
                    prompt=aa_prompt3,
                    tag=f"autoanswer_from_decide_after_recall_b{batch_idx}",
                    batch_id=f"b{batch_idx}.from_decide.after_recall",
                )
                if aa_obj3 is None:
                    aa_retry2 = _empty_auto_answer()
                    if aa3_state == "skipped":
                        aa_retry2["notes"] = "skipped: mind_circuit_open (auto_answer_to_codex from decide_next after recall)"
                    else:
                        aa_retry2["notes"] = "mind_error: auto_answer_to_codex(from decide_next after recall) failed; see EvidenceLog kind=mind_error"
                else:
                    aa_retry2 = aa_obj3

                append_jsonl(
                    project_paths.evidence_log_path,
                    {
                        "kind": "auto_answer",
                        "batch_id": f"b{batch_idx}.from_decide.after_recall",
                        "ts": now_rfc3339(),
                        "thread_id": thread_id,
                        "mind_transcript_ref": aa3_ref,
                        "auto_answer": aa_retry2,
                    },
                )
                evidence_window.append({"kind": "auto_answer", "batch_id": f"b{batch_idx}.from_decide.after_recall", **aa_retry2})
                evidence_window[:] = evidence_window[-8:]
                _segment_add({"kind": "auto_answer", "batch_id": f"b{batch_idx}.from_decide.after_recall", **(aa_retry2 if isinstance(aa_retry2, dict) else {})})
                _persist_segment_state()

                aa_text3 = ""
                if isinstance(aa_retry2, dict) and bool(aa_retry2.get("should_answer", False)):
                    aa_text3 = str(aa_retry2.get("codex_answer_input") or "").strip()
                chk_text3 = ""
                if isinstance(checks_obj, dict) and bool(checks_obj.get("should_run_checks", False)):
                    chk_text3 = str(checks_obj.get("codex_check_input") or "").strip()
                combined3 = "\n\n".join([x for x in [aa_text3, chk_text3] if x])
                if combined3:
                    if not _queue_next_input(
                        nxt=combined3,
                        codex_last_message=codex_last,
                        batch_id=f"b{batch_idx}.from_decide.after_recall",
                        reason="auto-answered (after recall) instead of prompting user",
                    ):
                        break
                    continue

                if isinstance(aa_retry2, dict) and bool(aa_retry2.get("needs_user_input", False)):
                    q3 = str(aa_retry2.get("ask_user_question") or "").strip()
                    if q3:
                        q = q3

            answer = _read_user_answer(q or "Need more information:")
            if not answer:
                status = "blocked"
                notes = "user did not provide required input"
                break
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "user_input",
                    "batch_id": f"b{batch_idx}",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "question": q,
                    "answer": answer,
                },
            )
            evidence_window.append({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": answer})
            evidence_window = evidence_window[-8:]
            _segment_add({"kind": "user_input", "batch_id": f"b{batch_idx}", "question": q, "answer": answer})
            _persist_segment_state()

            # Re-decide with the user input included (no extra Hands run yet).
            decision_prompt2 = decide_next_prompt(
                task=task,
                hands_provider=cur_provider,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                active_workflow=_active_workflow(),
                workflow_run=workflow_run if isinstance(workflow_run, dict) else {},
                recent_evidence=evidence_window,
                codex_last_message=codex_last,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                auto_answer=_empty_auto_answer(),
            )
            decision_obj2, decision2_mind_ref, decision2_state = _mind_call(
                schema_filename="decide_next.json",
                prompt=decision_prompt2,
                tag=f"decide_after_user_b{batch_idx}",
                batch_id=f"b{batch_idx}.after_user",
            )
            if decision_obj2 is None:
                # Safe fallback: send the user answer to Hands (optionally with checks),
                # rather than blocking on a missing post-user decision.
                chk_text2 = ""
                if isinstance(checks_obj, dict) and bool(checks_obj.get("should_run_checks", False)):
                    chk_text2 = str(checks_obj.get("codex_check_input") or "").strip()
                combined_user2 = "\n\n".join([x for x in [answer.strip(), chk_text2] if x])
                if not _queue_next_input(
                    nxt=combined_user2,
                    codex_last_message=codex_last,
                    batch_id=f"b{batch_idx}.after_user",
                    reason=(
                        "mind_circuit_open(decide_next after user): send user answer"
                        if decision2_state == "skipped"
                        else "mind_error(decide_next after user): send user answer"
                    ),
                ):
                    break
                continue

            decision_obj = decision_obj2
            _log_decide_next(
                decision_obj=decision_obj,
                batch_id=f"b{batch_idx}",
                phase="after_user",
                mind_transcript_ref=decision2_mind_ref,
            )

            # Apply overlay + learned from the post-user decision.
            overlay_update = decision_obj.get("update_project_overlay") or {}
            if isinstance(overlay_update, dict):
                set_tls = overlay_update.get("set_testless_strategy")
                if isinstance(set_tls, dict):
                    strategy = str(set_tls.get("strategy") or "").strip()
                    rationale = str(set_tls.get("rationale") or "").strip()
                    if strategy:
                        loaded.project_overlay.setdefault("testless_verification_strategy", {})
                        loaded.project_overlay["testless_verification_strategy"] = {
                            "chosen_once": True,
                            "strategy": strategy,
                            "rationale": rationale,
                        }
                        store.write_project_overlay(project_path, loaded.project_overlay)

            applied2 = _handle_learned_changes(
                learned_changes=decision_obj.get("learned_changes"),
                batch_id=f"b{batch_idx}.after_user",
                source="decide_next.after_user",
                mind_transcript_ref=decision2_mind_ref,
            )

            if applied2:
                loaded = store.load(project_path)
                _refresh_overlay_refs()

            next_action = str(decision_obj.get("next_action") or "stop")
            status = str(decision_obj.get("status") or "not_done")
            notes = str(decision_obj.get("notes") or "")

            if next_action == "stop":
                break
            if next_action == "send_to_codex":
                nxt = str(decision_obj.get("next_codex_input") or "").strip()
                if not nxt:
                    status = "blocked"
                    notes = "decide_next returned send_to_codex without next_codex_input (after user input)"
                    break
                if not _queue_next_input(
                    nxt=nxt,
                    codex_last_message=codex_last,
                    batch_id=f"b{batch_idx}.after_user",
                    reason="send_to_codex after user input",
                ):
                    break
                continue

            status = "blocked"
            notes = f"unexpected next_action={next_action} after user input"
            break

        if next_action == "send_to_codex":
            nxt = str(decision_obj.get("next_codex_input") or "").strip()
            if not nxt:
                status = "blocked"
                notes = "decide_next returned send_to_codex without next_codex_input"
                break
            if not _queue_next_input(
                nxt=nxt,
                codex_last_message=codex_last,
                batch_id=f"b{batch_idx}",
                reason="send_to_codex",
            ):
                break
            continue

        status = "blocked"
        notes = f"unknown next_action={next_action}"
        break
    else:
        max_batches_exhausted = True
        status = "blocked"
        notes = f"reached max_batches={max_batches}"

    # Final checkpoint for long-running sessions: mine when the model judges a boundary (done/blocked/max_batches),
    # even if the run ended without queueing a next Hands batch.
    if checkpoint_enabled and executed_batches > 0 and last_batch_id:
        final_hint = "max_batches" if max_batches_exhausted else str(status or "")
        _maybe_checkpoint_and_mine(
            batch_id=last_batch_id,
            planned_next_input="",
            status_hint=final_hint,
            note="run_end",
        )

    return AutopilotResult(
        status=status,
        thread_id=thread_id or "unknown",
        project_dir=project_paths.project_dir,
        evidence_log_path=project_paths.evidence_log_path,
        transcripts_dir=project_paths.transcripts_dir,
        batches=executed_batches,
        notes=notes,
    )
