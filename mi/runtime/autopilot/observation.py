from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ...providers.types import HandsRunResult
from ..risk import detect_risk_signals_from_command, detect_risk_signals_from_text_line
from ..transcript import summarize_codex_events, summarize_hands_transcript, open_transcript_text


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _batch_summary(result: HandsRunResult) -> dict[str, Any]:
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


def _detect_risk_signals(result: HandsRunResult) -> list[str]:
    signals: list[str] = []
    for item in result.iter_command_executions():
        cmd = str(item.get("command") or "")
        signals.extend(detect_risk_signals_from_command(cmd))
    seen: set[str] = set()
    out: list[str] = []
    for s in signals:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _detect_risk_signals_from_transcript(transcript_path: Path) -> list[str]:
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

    seen: set[str] = set()
    out: list[str] = []
    for s in signals:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _observe_repo(project_root: Path) -> dict[str, Any]:
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

    for name in ("pytest.ini", "tox.ini"):
        if exists(name):
            has_tests = True
            test_hints.append(name)

    for name in ("tests", "test"):
        p = root / name
        if p.is_dir():
            test_hints.append(f"{name}/")
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

    pkg = root / "package.json"
    if pkg.is_file():
        try:
            obj = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = obj.get("scripts") if isinstance(obj, dict) else None
            test_script = scripts.get("test") if isinstance(scripts, dict) else None
            if isinstance(test_script, str) and test_script.strip():
                has_tests = True
                test_hints.append("package.json scripts.test")
        except Exception:
            pass

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
