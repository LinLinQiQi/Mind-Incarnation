from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.project import load_project_overlay
from mi.providers.codex_runner import CodexRunResult
from mi.runtime.autopilot.services.testless_strategy_service import mk_testless_strategy_claim_text
from mi.runtime.runner import run_autopilot
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.pins import TESTLESS_STRATEGY_TAG


@dataclass(frozen=True)
class _FakePromptResult:
    obj: dict
    transcript_path: Path


class _FakeLlm:
    def __init__(self, responses_by_schema: dict[str, list[dict]]):
        self._responses_by_schema = {k: list(v) for k, v in responses_by_schema.items()}

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _FakePromptResult:
        q = self._responses_by_schema.get(schema_filename)
        if not q:
            raise AssertionError(f"FakeLlm: unexpected call schema={schema_filename}")
        obj = q.pop(0)
        return _FakePromptResult(obj=obj, transcript_path=Path("fake_mind.jsonl"))


class _FakeHands:
    def __init__(self, results: list[CodexRunResult]):
        self._results = list(results)

    def exec(self, **_kwargs) -> CodexRunResult:
        if not self._results:
            raise AssertionError("FakeHands: no more results")
        return self._results.pop(0)

    def resume(self, **_kwargs) -> CodexRunResult:
        if not self._results:
            raise AssertionError("FakeHands: no more results")
        return self._results.pop(0)


def _mk_result(*, thread_id: str) -> CodexRunResult:
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
    ]
    return CodexRunResult(thread_id=thread_id, exit_code=0, events=events, raw_transcript_path=Path("fake.jsonl"))


class TestRunnerTestlessStrategySeed(unittest.TestCase):
    def test_run_start_mirrors_tls_claim_into_overlay_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            tls_claim_id = tdb.append_claim_create(
                claim_type="preference",
                text=mk_testless_strategy_claim_text("Run smoke + manual checks"),
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[TESTLESS_STRATEGY_TAG],
                source_event_ids=["ev_seed_tls_1"],
                confidence=1.0,
                notes="seed tls claim",
            )
            self.assertTrue(bool(tls_claim_id))

            fake_hands = _FakeHands([_mk_result(thread_id="t1")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                    ],
                    "plan_min_checks.json": [
                        {
                            "should_run_checks": False,
                            "needs_testless_strategy": False,
                            "testless_strategy_question": "",
                            "check_goals": [],
                            "commands_hints": [],
                            "hands_check_input": "",
                            "notes": "skip",
                        }
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_hands_input": "",
                            "ask_user_question": "",
                            "learn_suggested": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        }
                    ],
                    "checkpoint_decide.json": [
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "skip",
                        }
                    ],
                    "suggest_workflow.json": [
                        {"should_suggest": False, "suggestion": None, "notes": "skip"},
                    ],
                    "mine_preferences.json": [
                        {"suggestions": [], "notes": "skip"},
                    ],
                    "mine_claims.json": [
                        {"claims": [], "edges": [], "notes": "skip"},
                    ],
                }
            )

            res = run_autopilot(
                task="x",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
                hands_provider="codex",
            )
            self.assertEqual(res.status, "done")

            overlay = load_project_overlay(home_dir=Path(home), project_root=Path(project_root))
            tls = overlay.get("testless_verification_strategy")
            self.assertIsInstance(tls, dict)
            self.assertTrue(bool((tls or {}).get("chosen_once", False)))
            self.assertEqual(str((tls or {}).get("claim_id") or "").strip(), tls_claim_id)
            self.assertIn("derived from Thought DB", str((tls or {}).get("rationale") or ""))


if __name__ == "__main__":
    unittest.main()
