from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.core.config import config_path, default_config
from mi.core.paths import ProjectPaths
from mi.core.storage import write_json
from mi.providers.codex_runner import CodexRunResult
from mi.runtime.runner import run_autopilot


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
        return self.exec(**_kwargs)


def _mk_done_result(*, thread_id: str) -> CodexRunResult:
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "All done."}},
    ]
    return CodexRunResult(thread_id=thread_id, exit_code=0, events=events, raw_transcript_path=Path("fake.jsonl"))


class TestWorkflowMiningAndTrigger(unittest.TestCase):
    def test_mine_solidify_then_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            cfg = default_config()
            cfg["runtime"]["workflows"]["auto_mine"] = True
            cfg["runtime"]["workflows"]["auto_enable"] = True
            cfg["runtime"]["workflows"]["min_occurrences"] = 2
            cfg["runtime"]["workflows"]["allow_single_if_high_benefit"] = True
            cfg["runtime"]["workflows"]["auto_sync_on_change"] = True
            write_json(config_path(Path(home)), cfg)

            # Run 1: mine a candidate (count=1), but do not solidify yet.
            fake_hands_1 = _FakeHands([_mk_done_result(thread_id="t1")])
            fake_llm_1 = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ok"], "actions": [], "results": ["done"], "unknowns": [], "risk_signals": []},
                    ],
                    "workflow_progress.json": [
                        {
                            "should_update": False,
                            "completed_step_ids": [],
                            "next_step_id": "",
                            "should_close": False,
                            "close_reason": "",
                            "confidence": 0.4,
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
                            "should_checkpoint": True,
                            "checkpoint_kind": "done",
                            "should_mine_workflow": True,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "mine workflow at done boundary",
                        }
                    ],
                    "suggest_workflow.json": [
                        {
                            "should_suggest": True,
                            "suggestion": {
                                "signature": "sig_deploy_v1",
                                "benefit": "medium",
                                "confidence": 0.7,
                                "reason": "repeatable deploy workflow",
                                "workflow": {
                                    "version": "v1",
                                    "id": "",
                                    "name": "Deploy",
                                    "enabled": False,
                                    "trigger": {"mode": "task_contains", "pattern": "deploy"},
                                    "mermaid": "",
                                    "steps": [
                                        {
                                            "id": "s1",
                                            "kind": "hands",
                                            "title": "Prepare",
                                            "hands_input": "Prepare release notes.",
                                            "check_input": "",
                                            "risk_category": "other",
                                            "policy": "values_judged",
                                            "notes": "",
                                        }
                                    ],
                                    "source": {"kind": "suggested", "reason": "from MI", "evidence_refs": []},
                                    "created_ts": "2026-01-01T00:00:00Z",
                                    "updated_ts": "2026-01-01T00:00:00Z",
                                },
                            },
                            "notes": "ok",
                        }
                    ],
                    "mine_claims.json": [
                        {"claims": [], "edges": [], "notes": "skip"}
                    ],
                }
            )

            _r1 = run_autopilot(
                task="deploy the app",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands_1.exec,
                hands_resume=fake_hands_1.resume,
                llm=fake_llm_1,
            )

            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            cand_path = pp.workflow_candidates_path
            obj1 = json.loads(cand_path.read_text(encoding="utf-8"))
            self.assertEqual(obj1["by_signature"]["sig_deploy_v1"]["count"], 1)

            wf_ids_1 = sorted([p.stem for p in pp.workflows_dir.glob("wf_*.json")]) if pp.workflows_dir.exists() else []
            self.assertEqual(wf_ids_1, [])

            # Run 2: same signature again (count=2) => solidify and enable by default.
            fake_hands_2 = _FakeHands([_mk_done_result(thread_id="t2")])
            fake_llm_2 = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ok"], "actions": [], "results": ["done"], "unknowns": [], "risk_signals": []},
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
                            "should_checkpoint": True,
                            "checkpoint_kind": "done",
                            "should_mine_workflow": True,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "mine workflow at done boundary",
                        }
                    ],
                    "suggest_workflow.json": [
                        {
                            "should_suggest": True,
                            "suggestion": {
                                "signature": "sig_deploy_v1",
                                "benefit": "medium",
                                "confidence": 0.7,
                                "reason": "repeatable deploy workflow",
                                "workflow": {
                                    "version": "v1",
                                    "id": "",
                                    "name": "Deploy",
                                    "enabled": False,
                                    "trigger": {"mode": "task_contains", "pattern": "deploy"},
                                    "mermaid": "",
                                    "steps": [
                                        {
                                            "id": "s1",
                                            "kind": "hands",
                                            "title": "Prepare",
                                            "hands_input": "Prepare release notes.",
                                            "check_input": "",
                                            "risk_category": "other",
                                            "policy": "values_judged",
                                            "notes": "",
                                        }
                                    ],
                                    "source": {"kind": "suggested", "reason": "from MI", "evidence_refs": []},
                                    "created_ts": "2026-01-01T00:00:00Z",
                                    "updated_ts": "2026-01-01T00:00:00Z",
                                },
                            },
                            "notes": "ok",
                        }
                    ],
                    "mine_claims.json": [
                        {"claims": [], "edges": [], "notes": "skip"}
                    ],
                }
            )

            _r2 = run_autopilot(
                task="deploy the app",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands_2.exec,
                hands_resume=fake_hands_2.resume,
                llm=fake_llm_2,
            )

            obj2 = json.loads(cand_path.read_text(encoding="utf-8"))
            self.assertEqual(obj2["by_signature"]["sig_deploy_v1"]["count"], 2)
            wf_id = obj2["by_signature"]["sig_deploy_v1"].get("workflow_id") or ""
            self.assertTrue(str(wf_id).startswith("wf_"))

            wf_path = pp.workflows_dir / f"{wf_id}.json"
            wf_obj = json.loads(wf_path.read_text(encoding="utf-8"))
            self.assertTrue(bool(wf_obj.get("enabled", False)))

            # Run 3: task triggers the enabled workflow => MI injects it into the first batch input.
            fake_hands_3 = _FakeHands([_mk_done_result(thread_id="t3")])
            fake_llm_3 = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ok"], "actions": [], "results": ["done"], "unknowns": [], "risk_signals": []},
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
                            "notes": "no",
                        }
                    ],
                }
            )

            r3 = run_autopilot(
                task="deploy the app",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands_3.exec,
                hands_resume=fake_hands_3.resume,
                llm=fake_llm_3,
            )

            last_input = ""
            with r3.evidence_log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "hands_input":
                        last_input = str(obj.get("input") or "")
            self.assertIn("MI Workflow Triggered", last_input)


if __name__ == "__main__":
    unittest.main()
