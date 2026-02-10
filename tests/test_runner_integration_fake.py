import io
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.codex_runner import CodexRunResult
from mi.mind_errors import MindCallError
from mi.mindspec import MindSpecStore
from mi.runner import run_autopilot


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
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, dict):
            raise AssertionError(f"FakeLlm: expected dict or exception, got {type(item)} for schema={schema_filename}")
        obj = item
        return _FakePromptResult(obj=obj, transcript_path=Path("fake_mind.jsonl"))


class _FakeHands:
    def __init__(self, results: list[CodexRunResult]):
        self._results = list(results)
        self.exec_calls = 0
        self.resume_calls = 0

    def exec(self, **kwargs) -> CodexRunResult:
        self.exec_calls += 1
        if not self._results:
            raise AssertionError("FakeHands: no more results")
        return self._results.pop(0)

    def resume(self, **kwargs) -> CodexRunResult:
        self.resume_calls += 1
        if not self._results:
            raise AssertionError("FakeHands: no more results")
        return self._results.pop(0)


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


class TestRunnerIntegrationFake(unittest.TestCase):
    def test_skip_plan_min_checks_when_evidence_is_sufficient(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t1", last_message="All done.", command="ls"),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ran ls"], "actions": [], "results": ["listed files"], "unknowns": [], "risk_signals": []},
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_codex_input": "",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        },
                    ],
                }
            )

            result = run_autopilot(
                task="smoke task",
                project_root=project_root,
                home_dir=home,
                max_batches=3,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )

            self.assertEqual(result.status, "done")
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "decide_next.json"])

            kinds = set()
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.add(obj["kind"])
            self.assertIn("hands_input", kinds)
            self.assertIn("check_plan", kinds)
            self.assertIn("decide_next", kinds)

    def test_loop_guard_blocks_when_repeating_and_ask_when_uncertain_false(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            store = MindSpecStore(home_dir=home)
            base = store.load_base()
            base.setdefault("defaults", {})
            base["defaults"]["ask_when_uncertain"] = False
            store.write_base(base)

            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t2", last_message="Still working."),
                    _mk_result(thread_id="t2", last_message="Still working."),
                    _mk_result(thread_id="t2", last_message="Still working."),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "send_to_codex",
                            "status": "not_done",
                            "confidence": 0.8,
                            "next_codex_input": "do next",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "continue",
                        },
                        {
                            "next_action": "send_to_codex",
                            "status": "not_done",
                            "confidence": 0.8,
                            "next_codex_input": "do next",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "continue",
                        },
                        {
                            "next_action": "send_to_codex",
                            "status": "not_done",
                            "confidence": 0.8,
                            "next_codex_input": "do next",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "continue",
                        },
                    ],
                }
            )

            result = run_autopilot(
                task="start",
                project_root=project_root,
                home_dir=home,
                max_batches=6,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )

            self.assertEqual(result.status, "blocked")
            found_loop_guard = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "loop_guard":
                        found_loop_guard = True
                        break
            self.assertTrue(found_loop_guard)

    def test_risk_prompt_triggers_only_for_configured_severities(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            store = MindSpecStore(home_dir=home)
            base = store.load_base()
            base.setdefault("violation_response", {})
            base["violation_response"]["prompt_user_on_high_risk"] = True
            base["violation_response"]["prompt_user_risk_severities"] = ["high"]
            base["violation_response"]["prompt_user_respect_should_ask_user"] = True
            store.write_base(base)

            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t3", last_message="All done.", command="git push origin main"),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ran push"], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                    ],
                    "risk_judge.json": [
                        {"category": "push", "severity": "high", "should_ask_user": True, "mitigation": [], "learned_changes": []},
                    ],
                }
            )

            old_stdin = sys.stdin
            old_stderr = sys.stderr
            sys.stdin = io.StringIO("n\n")
            sys.stderr = io.StringIO()
            try:
                result = run_autopilot(
                    task="x",
                    project_root=project_root,
                    home_dir=home,
                    max_batches=1,
                    hands_exec=fake_hands.exec,
                    hands_resume=fake_hands.resume,
                    llm=fake_llm,
                )
            finally:
                sys.stdin = old_stdin
                sys.stderr = old_stderr

            self.assertEqual(result.status, "blocked")
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "risk_judge.json"])

            kinds = set()
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.add(obj["kind"])
            self.assertIn("risk_event", kinds)

    def test_risk_prompt_skips_when_severity_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            store = MindSpecStore(home_dir=home)
            base = store.load_base()
            base.setdefault("violation_response", {})
            base["violation_response"]["prompt_user_on_high_risk"] = True
            base["violation_response"]["prompt_user_risk_severities"] = ["high", "critical"]
            base["violation_response"]["prompt_user_respect_should_ask_user"] = True
            store.write_base(base)

            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t4", last_message="All done.", command="git push origin main"),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                    ],
                    "risk_judge.json": [
                        {"category": "push", "severity": "medium", "should_ask_user": True, "mitigation": [], "learned_changes": []},
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_codex_input": "",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        },
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

            self.assertEqual(result.status, "done")
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "risk_judge.json", "decide_next.json"])

    def test_mind_error_extract_evidence_is_logged_and_run_continues(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t5", last_message="All done.", command="ls"),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        MindCallError(
                            "boom",
                            schema_filename="extract_evidence.json",
                            tag="extract_b0",
                            transcript_path=Path("mind_extract.jsonl"),
                        )
                    ],
                    "plan_min_checks.json": [
                        {
                            "should_run_checks": False,
                            "needs_testless_strategy": False,
                            "testless_strategy_question": "",
                            "check_goals": [],
                            "commands_hints": [],
                            "codex_check_input": "",
                            "notes": "skip",
                        }
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_codex_input": "",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        },
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

            self.assertEqual(result.status, "done")
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "plan_min_checks.json", "decide_next.json"])

            kinds = []
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.append(obj["kind"])
            self.assertIn("mind_error", kinds)

    def test_mind_error_decide_next_blocks_when_ask_when_uncertain_false(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            store = MindSpecStore(home_dir=home)
            base = store.load_base()
            base.setdefault("defaults", {})
            base["defaults"]["ask_when_uncertain"] = False
            store.write_base(base)

            fake_hands = _FakeHands([_mk_result(thread_id="t6", last_message="Still working.")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                    ],
                    "decide_next.json": [
                        MindCallError(
                            "down",
                            schema_filename="decide_next.json",
                            tag="decide_b0",
                            transcript_path=Path("mind_decide.jsonl"),
                        )
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

            self.assertEqual(result.status, "blocked")

            found = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "mind_error":
                        if obj.get("schema_filename") == "decide_next.json":
                            found = True
                            break
            self.assertTrue(found)

    def test_mind_error_risk_judge_falls_back_and_logs_risk_event(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            store = MindSpecStore(home_dir=home)
            base = store.load_base()
            base.setdefault("violation_response", {})
            base["violation_response"]["prompt_user_on_high_risk"] = False
            store.write_base(base)

            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t7", last_message="All done.", command="git push origin main"),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                    ],
                    "risk_judge.json": [
                        MindCallError(
                            "boom",
                            schema_filename="risk_judge.json",
                            tag="risk_b0",
                            transcript_path=Path("mind_risk.jsonl"),
                        )
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_codex_input": "",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        },
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

            self.assertEqual(result.status, "done")
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "risk_judge.json", "decide_next.json"])

            kinds = set()
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.add(obj["kind"])
            self.assertIn("mind_error", kinds)
            self.assertIn("risk_event", kinds)


if __name__ == "__main__":
    unittest.main()
