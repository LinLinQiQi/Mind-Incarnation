import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.codex_runner import CodexRunResult
from mi.mindspec import MindSpecStore
from mi.runner import run_autopilot


@dataclass(frozen=True)
class _FakePromptResult:
    obj: dict
    transcript_path: Path


class _FakeLlm:
    def __init__(self, responses_by_schema: dict[str, list[dict]]):
        self._responses_by_schema = {k: list(v) for k, v in responses_by_schema.items()}
        self.calls: list[str] = []

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _FakePromptResult:
        self.calls.append(schema_filename)
        q = self._responses_by_schema.get(schema_filename)
        if not q:
            raise AssertionError(f"FakeLlm: unexpected call schema={schema_filename}")
        obj = q.pop(0)
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
            self.assertIn("codex_input", kinds)
            self.assertIn("check_plan", kinds)

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


if __name__ == "__main__":
    unittest.main()
