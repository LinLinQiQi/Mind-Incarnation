from __future__ import annotations

import sys
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_runner import run_codex_exec, run_codex_resume, CodexRunResult
from .codex_runner import InterruptConfig
from .llm import MiLlm
from .mindspec import MindSpecStore
from .paths import ProjectPaths, default_home_dir
from .prompts import decide_next_prompt, extract_evidence_prompt, plan_min_checks_prompt
from .prompts import auto_answer_to_codex_prompt
from .prompts import risk_judge_prompt
from .storage import append_jsonl, now_rfc3339, ensure_dir
from .transcript import summarize_codex_events


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

    transcript_observation = summarize_codex_events(result.events)

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
        lower = cmd.lower()
        if "git push" in lower:
            signals.append(f"push: {cmd}")
        if "npm publish" in lower or "twine upload" in lower:
            signals.append(f"publish: {cmd}")
        if "pip install" in lower or "npm install" in lower or "pnpm install" in lower or "yarn add" in lower:
            signals.append(f"install: {cmd}")
        if "curl " in lower or "wget " in lower:
            signals.append(f"network: {cmd}")
        if "rm -rf" in lower or " rm -r" in lower:
            signals.append(f"delete: {cmd}")
        if "sudo " in lower:
            signals.append(f"privilege: {cmd}")
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
) -> AutopilotResult:
    project_path = Path(project_root).resolve()
    home = Path(home_dir) if home_dir else default_home_dir()

    store = MindSpecStore(home_dir=str(home))
    loaded = store.load(project_path)

    project_paths = ProjectPaths(home_dir=home, project_root=project_path)
    ensure_dir(project_paths.project_dir)
    ensure_dir(project_paths.transcripts_dir)

    if llm is None:
        llm = MiLlm(project_root=project_path, transcripts_dir=project_paths.transcripts_dir)
    if hands_exec is None:
        hands_exec = run_codex_exec
    if hands_resume is _DEFAULT:
        hands_resume = run_codex_resume

    evidence_window: list[dict[str, Any]] = []
    thread_id: str | None = None
    next_input: str = task

    status = "not_done"
    notes = ""

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

    def _queue_next_input(*, nxt: str, codex_last_message: str, batch_id: str, reason: str) -> bool:
        """Set next_input for the next Codex batch, with loop-guard and optional user intervention."""
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
                    + "). Provide a new instruction to send to Codex, or type 'stop' to end:"
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

        next_input = candidate
        status = "not_done"
        notes = reason
        return True

    executed_batches = 0
    for batch_idx in range(max_batches):
        batch_id = f"b{batch_idx}"
        batch_ts = now_rfc3339().replace(":", "").replace("-", "")
        hands_transcript = project_paths.transcripts_dir / "hands" / f"{batch_ts}_b{batch_idx}.jsonl"

        light = loaded.light_injection()
        batch_input = next_input.strip()
        codex_prompt = light + "\n" + batch_input + "\n"
        sent_ts = now_rfc3339()
        prompt_sha256 = hashlib.sha256(codex_prompt.encode("utf-8")).hexdigest()

        if thread_id is None or hands_resume is None or thread_id == "unknown":
            result = hands_exec(
                prompt=codex_prompt,
                project_root=project_path,
                transcript_path=hands_transcript,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=interrupt_cfg,
            )
            if thread_id is None:
                thread_id = result.thread_id
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
            if getattr(result, "thread_id", ""):
                thread_id = result.thread_id

        executed_batches += 1

        # Persist exactly what MI sent to Codex (transparency + later audit).
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
            light_injection=light,
            batch_input=batch_input,
            codex_batch_summary=summary,
            repo_observation=repo_obs,
        )
        evidence_obj = llm.call(schema_filename="extract_evidence.json", prompt=extract_prompt, tag=f"extract_b{batch_idx}").obj
        evidence_item = {
            "batch_id": batch_id,
            "ts": now_rfc3339(),
            "thread_id": thread_id,
            "hands_transcript_ref": str(hands_transcript),
            "codex_transcript_ref": str(hands_transcript),  # legacy key (V1 early logs)
            "mi_input": batch_input,
            "transcript_observation": summary.get("transcript_observation") or {},
            "repo_observation": repo_obs,
            **evidence_obj,
        }
        append_jsonl(project_paths.evidence_log_path, evidence_item)
        evidence_window.append(evidence_item)
        evidence_window = evidence_window[-8:]

        # Post-hoc risk judgement (LLM) when heuristic signals are present.
        risk_signals = _detect_risk_signals(result)
        if risk_signals:
            risk_prompt = risk_judge_prompt(
                task=task,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                risk_signals=risk_signals,
                codex_last_message=result.last_agent_message(),
            )
            risk_obj = llm.call(schema_filename="risk_judge.json", prompt=risk_prompt, tag=f"risk_b{batch_idx}").obj
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "risk_event",
                    "batch_id": f"b{batch_idx}",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "risk_signals": risk_signals,
                    "risk": risk_obj,
                },
            )
            evidence_window.append({"kind": "risk_event", "batch_id": f"b{batch_idx}", **risk_obj})
            evidence_window = evidence_window[-8:]

            # Learned tightening suggestions from risk_judge.
            for ch in risk_obj.get("learned_changes") or []:
                if not isinstance(ch, dict):
                    continue
                scope = str(ch.get("scope") or "").strip()
                text = str(ch.get("text") or "").strip()
                rationale = str(ch.get("rationale") or "").strip()
                if scope in ("global", "project") and text:
                    store.append_learned(project_root=project_path, scope=scope, text=text, rationale=rationale or "risk_judge")

            loaded = store.load(project_path)

            # Optional immediate user escalation on high risk.
            vr = loaded.base.get("violation_response") or {}
            prompt_user = bool(vr.get("prompt_user_on_high_risk", True))
            severity = str(risk_obj.get("severity") or "low")
            should_ask_user = bool(risk_obj.get("should_ask_user", False))
            if prompt_user and should_ask_user and severity in ("high", "critical"):
                cat = str(risk_obj.get("category") or "other")
                mitig = risk_obj.get("mitigation") or []
                mitig_s = "; ".join([str(x) for x in mitig if str(x).strip()][:3])
                q = f"High-risk action detected (category={cat}, severity={severity}). Continue? (y/N)\nMitigation: {mitig_s}"
                answer = _read_user_answer(q)
                if answer.strip().lower() not in ("y", "yes"):
                    status = "blocked"
                    notes = "stopped after high-risk event"
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
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                recent_evidence=evidence_window,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            )
            checks_obj = llm.call(schema_filename="plan_min_checks.json", prompt=checks_prompt, tag=f"checks_b{batch_idx}").obj
        else:
            checks_obj = _empty_check_plan()
            checks_obj["notes"] = "skipped: no uncertainty/risk/question detected"
        append_jsonl(
            project_paths.evidence_log_path,
            {
                "kind": "check_plan",
                "batch_id": f"b{batch_idx}",
                "ts": now_rfc3339(),
                "thread_id": thread_id,
                "checks": checks_obj,
            },
        )
        evidence_window.append({"kind": "check_plan", "batch_id": f"b{batch_idx}", **checks_obj})
        evidence_window = evidence_window[-8:]

        # Auto-answer Codex when it is asking the user questions; only ask the user if MI cannot answer.
        auto_answer_obj = _empty_auto_answer()
        if _looks_like_user_question(codex_last):
            aa_prompt = auto_answer_to_codex_prompt(
                task=task,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                recent_evidence=evidence_window,
                codex_last_message=codex_last,
            )
            try:
                auto_answer_obj = llm.call(schema_filename="auto_answer_to_codex.json", prompt=aa_prompt, tag=f"autoanswer_b{batch_idx}").obj
            except Exception as e:
                auto_answer_obj = _empty_auto_answer()
                auto_answer_obj["notes"] = f"auto_answer_to_codex failed: {e}"
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "auto_answer",
                    "batch_id": f"b{batch_idx}",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "auto_answer": auto_answer_obj,
                },
            )
            evidence_window.append({"kind": "auto_answer", "batch_id": f"b{batch_idx}", **auto_answer_obj})
            evidence_window = evidence_window[-8:]

        # Deterministic pre-action arbitration to minimize user burden:
        # 1) If auto_answer requires user input -> ask user, then send answer to Codex (optionally with checks).
        # 2) If minimal checks require a testless verification strategy and it hasn't been chosen -> ask once and persist.
        # 3) If MI can answer Codex and/or run minimal checks -> send to Codex (skip decide_next for this iteration).
        if isinstance(auto_answer_obj, dict) and bool(auto_answer_obj.get("needs_user_input", False)):
            q = str(auto_answer_obj.get("ask_user_question") or "").strip() or codex_last.strip() or "Need more information:"
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

            loaded.project_overlay.setdefault("testless_verification_strategy", {})
            loaded.project_overlay["testless_verification_strategy"] = {
                "chosen_once": True,
                "strategy": answer.strip(),
                "rationale": "user provided testless verification strategy",
            }
            store.write_project_overlay(project_path, loaded.project_overlay)
            loaded = store.load(project_path)

            # Re-plan checks now that the project has a strategy to follow.
            checks_prompt2 = plan_min_checks_prompt(
                task=task,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                recent_evidence=evidence_window,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            )
            checks_obj = llm.call(schema_filename="plan_min_checks.json", prompt=checks_prompt2, tag=f"checks_after_tls_b{batch_idx}").obj
            append_jsonl(
                project_paths.evidence_log_path,
                {
                    "kind": "check_plan",
                    "batch_id": f"b{batch_idx}.after_testless",
                    "ts": now_rfc3339(),
                    "thread_id": thread_id,
                    "checks": checks_obj,
                },
            )
            evidence_window.append({"kind": "check_plan", "batch_id": f"b{batch_idx}.after_testless", **checks_obj})
            evidence_window = evidence_window[-8:]

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
                reason="sent auto-answer/checks to Codex",
            ):
                break
            continue

        # Decide what to do next.
        decision_prompt = decide_next_prompt(
            task=task,
            mindspec_base=loaded.base,
            learned_text=loaded.learned_text,
            project_overlay=loaded.project_overlay,
            recent_evidence=evidence_window,
            codex_last_message=codex_last,
            repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
            check_plan=checks_obj if isinstance(checks_obj, dict) else {},
            auto_answer=auto_answer_obj,
        )
        decision_obj = llm.call(schema_filename="decide_next.json", prompt=decision_prompt, tag=f"decide_b{batch_idx}").obj

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
        for ch in decision_obj.get("learned_changes") or []:
            if not isinstance(ch, dict):
                continue
            scope = str(ch.get("scope") or "").strip()
            text = str(ch.get("text") or "").strip()
            rationale = str(ch.get("rationale") or "").strip()
            if scope in ("global", "project") and text:
                store.append_learned(project_root=project_path, scope=scope, text=text, rationale=rationale or "auto")

        # Refresh loaded learned text after any new writes.
        loaded = store.load(project_path)

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
                    mindspec_base=loaded.base,
                    learned_text=loaded.learned_text,
                    project_overlay=loaded.project_overlay,
                    repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                    check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                    recent_evidence=evidence_window,
                    codex_last_message=q,
                )
                try:
                    aa_from_decide = llm.call(
                        schema_filename="auto_answer_to_codex.json",
                        prompt=aa_prompt2,
                        tag=f"autoanswer_from_decide_b{batch_idx}",
                    ).obj
                except Exception as e:
                    aa_from_decide = _empty_auto_answer()
                    aa_from_decide["notes"] = f"auto_answer_to_codex(from decide_next) failed: {e}"

                append_jsonl(
                    project_paths.evidence_log_path,
                    {
                        "kind": "auto_answer",
                        "batch_id": f"b{batch_idx}.from_decide",
                        "ts": now_rfc3339(),
                        "thread_id": thread_id,
                        "auto_answer": aa_from_decide,
                    },
                )
                evidence_window.append({"kind": "auto_answer", "batch_id": f"b{batch_idx}.from_decide", **aa_from_decide})
                evidence_window = evidence_window[-8:]

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

            # Re-decide with the user input included (no extra Codex run yet).
            decision_prompt2 = decide_next_prompt(
                task=task,
                mindspec_base=loaded.base,
                learned_text=loaded.learned_text,
                project_overlay=loaded.project_overlay,
                recent_evidence=evidence_window,
                codex_last_message=codex_last,
                repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
                check_plan=checks_obj if isinstance(checks_obj, dict) else {},
                auto_answer=_empty_auto_answer(),
            )
            decision_obj = llm.call(
                schema_filename="decide_next.json",
                prompt=decision_prompt2,
                tag=f"decide_after_user_b{batch_idx}",
            ).obj

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

            for ch in decision_obj.get("learned_changes") or []:
                if not isinstance(ch, dict):
                    continue
                scope = str(ch.get("scope") or "").strip()
                text = str(ch.get("text") or "").strip()
                rationale = str(ch.get("rationale") or "").strip()
                if scope in ("global", "project") and text:
                    store.append_learned(project_root=project_path, scope=scope, text=text, rationale=rationale or "auto")

            loaded = store.load(project_path)

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

    return AutopilotResult(
        status=status,
        thread_id=thread_id or "unknown",
        project_dir=project_paths.project_dir,
        evidence_log_path=project_paths.evidence_log_path,
        transcripts_dir=project_paths.transcripts_dir,
        batches=executed_batches,
        notes=notes,
    )
