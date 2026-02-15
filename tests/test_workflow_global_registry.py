import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.codex_runner import CodexRunResult
from mi.paths import GlobalPaths, ProjectPaths
from mi.runner import run_autopilot
from mi.workflows import GlobalWorkflowStore, WorkflowRegistry, WorkflowStore


def _mk_result(*, thread_id: str, last_message: str) -> CodexRunResult:
    events = [{"type": "thread.started", "thread_id": thread_id}]
    events.append({"type": "item.completed", "item": {"type": "agent_message", "text": last_message}})
    return CodexRunResult(thread_id=thread_id, exit_code=0, events=events, raw_transcript_path=Path("fake.jsonl"))


@dataclass(frozen=True)
class _FakePromptResult:
    obj: dict
    transcript_path: Path


class _FakeLlm:
    def __init__(self, responses_by_schema: dict[str, list[object]]):
        self._responses_by_schema = {k: list(v) for k, v in responses_by_schema.items()}

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _FakePromptResult:
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


class TestWorkflowGlobalRegistry(unittest.TestCase):
    def test_registry_project_precedence_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            home_p = Path(home)
            pp = ProjectPaths(home_dir=home_p, project_root=Path(project_root))
            proj = WorkflowStore(pp)
            glob = GlobalWorkflowStore(GlobalPaths(home_dir=home_p))
            reg = WorkflowRegistry(project_store=proj, global_store=glob)

            w_global = {
                "version": "v1",
                "id": "wf_shared",
                "name": "Global Shared",
                "enabled": True,
                "trigger": {"mode": "task_contains", "pattern": "deploy"},
                "mermaid": "",
                "steps": [],
                "source": {"kind": "manual", "reason": "", "evidence_refs": []},
                "created_ts": "2026-01-01T00:00:00Z",
                "updated_ts": "2026-01-01T00:00:00Z",
            }
            glob.write(w_global)

            # Global enabled shows up by default.
            eff1 = reg.enabled_workflows_effective(overlay={})
            self.assertEqual([w.get("id") for w in eff1], ["wf_shared"])
            self.assertEqual(str(eff1[0].get("_mi_scope")), "global")

            # Project overlay can disable the global workflow for this project.
            eff2 = reg.enabled_workflows_effective(overlay={"global_workflow_overrides": {"wf_shared": {"enabled": False}}})
            self.assertEqual(eff2, [])

            # Project workflow shadows global even when disabled (project precedence).
            proj.write(dict(w_global, name="Project Shadow", enabled=False))
            eff3 = reg.enabled_workflows_effective(overlay={})
            self.assertEqual(eff3, [])

            # Project workflow wins over overlay-disabled global when enabled.
            proj.write(dict(w_global, name="Project Wins", enabled=True))
            eff4 = reg.enabled_workflows_effective(overlay={"global_workflow_overrides": {"wf_shared": {"enabled": False}}})
            self.assertEqual([w.get("id") for w in eff4], ["wf_shared"])
            self.assertEqual(str(eff4[0].get("_mi_scope")), "project")

    def test_run_autopilot_can_trigger_global_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            home_p = Path(home)
            glob = GlobalWorkflowStore(GlobalPaths(home_dir=home_p))
            glob.write(
                {
                    "version": "v1",
                    "id": "wf_global_trigger",
                    "name": "Global Trigger",
                    "enabled": True,
                    "trigger": {"mode": "task_contains", "pattern": "deploy"},
                    "mermaid": "",
                    "steps": [],
                    "source": {"kind": "manual", "reason": "", "evidence_refs": []},
                    "created_ts": "2026-01-01T00:00:00Z",
                    "updated_ts": "2026-01-01T00:00:00Z",
                }
            )

            fake_hands = _FakeHands([_mk_result(thread_id="t1", last_message="done")])
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
                        },
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

            r = run_autopilot(
                task="deploy the app",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )
            last_input = ""
            with r.evidence_log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "hands_input":
                        last_input = str(obj.get("input") or "")
            self.assertIn("MI Workflow Triggered", last_input)
            self.assertIn("wf_global_trigger", last_input)


if __name__ == "__main__":
    unittest.main()

