import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.mindspec import MindSpecStore
from mi.providers.codex_runner import CodexRunResult
from mi.runtime.runner import run_autopilot


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

    def exec(self, **_kwargs) -> CodexRunResult:
        self.exec_calls += 1
        if not self._results:
            raise AssertionError("FakeHands: no more results")
        return self._results.pop(0)

    def resume(self, **_kwargs) -> CodexRunResult:
        self.resume_calls += 1
        if not self._results:
            raise AssertionError("FakeHands: no more results")
        return self._results.pop(0)


def _mk_result(*, thread_id: str) -> CodexRunResult:
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
    ]
    return CodexRunResult(thread_id=thread_id, exit_code=0, events=events, raw_transcript_path=Path("fake.jsonl"))


class TestRunnerHandsPersistence(unittest.TestCase):
    def test_persist_and_resume_hands_thread_id_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            root = Path(project_root)

            # Run 1: exec should be used; MI persists hands_state.thread_id to overlay.json.
            fake_hands_1 = _FakeHands([_mk_result(thread_id="t123")])
            fake_llm_1 = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
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
            r1 = run_autopilot(
                task="x",
                project_root=str(root),
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands_1.exec,
                hands_resume=fake_hands_1.resume,
                llm=fake_llm_1,
                hands_provider="codex",
            )
            self.assertEqual(r1.status, "done")
            self.assertEqual(fake_hands_1.exec_calls, 1)

            store = MindSpecStore(home_dir=home)
            overlay = store.load_project_overlay(root)
            hs = overlay.get("hands_state") if isinstance(overlay.get("hands_state"), dict) else {}
            self.assertEqual(hs.get("provider"), "codex")
            self.assertEqual(hs.get("thread_id"), "t123")

            # Run 2: with continue_hands=true, MI should call resume on first batch.
            fake_hands_2 = _FakeHands([_mk_result(thread_id="t123")])
            fake_llm_2 = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
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
            r2 = run_autopilot(
                task="x",
                project_root=str(root),
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands_2.exec,
                hands_resume=fake_hands_2.resume,
                llm=fake_llm_2,
                hands_provider="codex",
                continue_hands=True,
            )
            self.assertEqual(r2.status, "done")
            self.assertEqual(fake_hands_2.exec_calls, 0)
            self.assertEqual(fake_hands_2.resume_calls, 1)

    def test_reset_hands_forces_fresh_session(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            root = Path(project_root)

            store = MindSpecStore(home_dir=home)
            overlay = store.load_project_overlay(root)
            overlay.setdefault("hands_state", {})
            overlay["hands_state"] = {"provider": "codex", "thread_id": "t123", "updated_ts": "t"}
            store.write_project_overlay(root, overlay)

            fake_hands = _FakeHands([_mk_result(thread_id="t999")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
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
            _ = run_autopilot(
                task="x",
                project_root=str(root),
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
                hands_provider="codex",
                continue_hands=True,
                reset_hands=True,
            )
            self.assertEqual(fake_hands.exec_calls, 1)
            self.assertEqual(fake_hands.resume_calls, 0)


if __name__ == "__main__":
    unittest.main()
