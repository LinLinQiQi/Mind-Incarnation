from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.config import config_path, default_config
from mi.core.paths import ProjectPaths
from mi.core.storage import write_json
from mi.providers.codex_runner import CodexRunResult
from mi.runtime.runner import run_autopilot
from mi.thoughtdb import ThoughtDbStore


@dataclass(frozen=True)
class _FakePromptResult:
    obj: dict
    transcript_path: Path


class _FakeLlm:
    def __init__(self, responses_by_schema: dict[str, list[object]]):
        self._responses_by_schema = {k: list(v) for k, v in responses_by_schema.items()}
        self.calls: list[str] = []

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _FakePromptResult:
        self.calls.append(schema_filename)
        q = self._responses_by_schema.get(schema_filename)
        if not q:
            raise AssertionError(f"FakeLlm: unexpected call schema={schema_filename}")
        item = q.pop(0)
        if not isinstance(item, dict):
            raise AssertionError(f"FakeLlm: expected dict, got {type(item)} for schema={schema_filename}")
        return _FakePromptResult(obj=item, transcript_path=Path("fake_mind.jsonl"))


class _FakeHands:
    def __init__(self, results: list[CodexRunResult]):
        self._results = list(results)

    def exec(self, **kwargs) -> CodexRunResult:
        if not self._results:
            raise AssertionError("FakeHands: no more results")
        return self._results.pop(0)

    def resume(self, **kwargs) -> CodexRunResult:
        return self.exec(**kwargs)


def _mk_result(*, thread_id: str, last_message: str, command: str = "") -> CodexRunResult:
    events = [{"type": "thread.started", "thread_id": thread_id}]
    if command:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": command,
                    "exit_code": 0,
                    "aggregated_output": "ok",
                },
            }
        )
    events.append({"type": "item.completed", "item": {"type": "agent_message", "text": last_message}})
    return CodexRunResult(thread_id=thread_id, exit_code=0, events=events, raw_transcript_path=Path("fake.jsonl"))


class TestLearnSuggestedApply(unittest.TestCase):
    def test_auto_learn_false_records_suggestion_and_does_not_write_learned(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            cfg = default_config()
            cfg["runtime"]["violation_response"]["auto_learn"] = False
            write_json(config_path(Path(home)), cfg)

            fake_hands = _FakeHands([_mk_result(thread_id="t1", last_message="All done.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ran ls"], "actions": [], "results": ["ok"], "unknowns": [], "risk_signals": []},
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_hands_input": "",
                            "ask_user_question": "",
                            "learn_suggested": [
                                {
                                    "scope": "project",
                                    "severity": "low",
                                    "text": "Do not auto-install dependencies without explicit confirmation.",
                                    "rationale": "safety preference",
                                }
                            ],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        }
                    ],
                    "suggest_workflow.json": [
                        {"should_suggest": False, "suggestion": None, "notes": "skip"},
                    ],
                    "mine_preferences.json": [
                        {"suggestions": [], "notes": "skip"},
                    ],
                }
            )

            result = run_autopilot(
                task="x",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )

            # Strict Thought DB mode: MI no longer writes learned.jsonl automatically.
            learned_path = result.project_dir / "learned.jsonl"
            self.assertFalse(learned_path.exists(), "auto_learn=false must not write learned.jsonl (strict Thought DB mode)")

            suggestion_id = ""
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "learn_suggested":
                        self.assertFalse(bool(obj.get("auto_learn")))
                        suggestion_id = str(obj.get("id") or "")
                        applied_claims = obj.get("applied_claim_ids") if isinstance(obj.get("applied_claim_ids"), list) else []
                        self.assertEqual(len(applied_claims), 0)
            self.assertTrue(suggestion_id.startswith("ls_"))

            # Apply it manually via CLI.
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", home, "claim", "apply-suggested", suggestion_id, "--cd", project_root])
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)

            # CLI apply-suggested materializes preference claims (not learned.jsonl).
            self.assertFalse(learned_path.exists(), "apply-suggested should not write learned.jsonl in strict Thought DB mode")
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            view = tdb.load_view(scope="project")
            texts = [str(c.get("text") or "") for c in view.iter_claims(include_inactive=True, include_aliases=True)]
            self.assertTrue(
                any("Do not auto-install dependencies" in t for t in texts),
                "apply-suggested should create a preference claim with the suggested text",
            )

            found_applied = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "learn_applied":
                        if obj.get("suggestion_id") == suggestion_id:
                            applied_claims = obj.get("applied_claim_ids") if isinstance(obj.get("applied_claim_ids"), list) else []
                            self.assertTrue(len(applied_claims) >= 1)
                            found_applied = True
                            break
            self.assertTrue(found_applied, "apply-suggested should append a learn_applied record")

    def test_auto_learn_true_writes_learned_and_logs_applied_ids(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            cfg = default_config()
            cfg["runtime"]["violation_response"]["auto_learn"] = True
            write_json(config_path(Path(home)), cfg)

            fake_hands = _FakeHands([_mk_result(thread_id="t2", last_message="All done.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ran ls"], "actions": [], "results": ["ok"], "unknowns": [], "risk_signals": []},
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_hands_input": "",
                            "ask_user_question": "",
                            "learn_suggested": [
                                {
                                    "scope": "project",
                                    "severity": "low",
                                    "text": "Assume behavior-preserving changes unless requested otherwise.",
                                    "rationale": "refactor preference",
                                }
                            ],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        }
                    ],
                    "suggest_workflow.json": [
                        {"should_suggest": False, "suggestion": None, "notes": "skip"},
                    ],
                    "mine_preferences.json": [
                        {"suggestions": [], "notes": "skip"},
                    ],
                }
            )

            result = run_autopilot(
                task="x",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )

            # Strict Thought DB mode: auto learning materializes preference Claims.
            learned_path = result.project_dir / "learned.jsonl"
            self.assertFalse(learned_path.exists(), "auto_learn=true should not write learned.jsonl in strict Thought DB mode")

            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            view = tdb.load_view(scope="project")
            texts = [str(c.get("text") or "") for c in view.iter_claims(include_inactive=True, include_aliases=True)]
            self.assertTrue(
                any("Assume behavior-preserving changes" in t for t in texts),
                "auto_learn=true should create a preference claim",
            )

            found = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict) or obj.get("kind") != "learn_suggested":
                        continue
                    if not bool(obj.get("auto_learn")):
                        continue
                    applied = obj.get("applied_claim_ids") if isinstance(obj.get("applied_claim_ids"), list) else []
                    if len(applied) == 1:
                        found = True
                        break
            self.assertTrue(found, "auto_learn=true should log learn_suggested with applied_claim_ids")


if __name__ == "__main__":
    unittest.main()
