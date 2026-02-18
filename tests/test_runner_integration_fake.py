from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.core.config import config_path, default_config
from mi.core.paths import GlobalPaths, ProjectPaths
from mi.core.storage import ensure_dir, iter_jsonl, write_json
from mi.memory.service import MemoryService
from mi.memory.types import MemoryItem
from mi.providers.codex_runner import CodexRunResult
from mi.providers.mind_errors import MindCallError
from mi.runtime.runner import run_autopilot
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.operational_defaults import ensure_operational_defaults_claims_current
from mi.thoughtdb.pins import TESTLESS_STRATEGY_TAG
from mi.thoughtdb.values import VALUES_BASE_TAG, write_values_set_event


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
    def test_run_records_state_corrupt_when_overlay_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            ensure_dir(pp.project_dir)
            pp.overlay_path.write_text("{", encoding="utf-8")

            fake_hands = _FakeHands([_mk_result(thread_id="t_corrupt", last_message="All done.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {
                            "facts": ["ran ls"],
                            "actions": [{"kind": "command", "detail": "ls"}],
                            "results": ["ok"],
                            "unknowns": [],
                            "risk_signals": [],
                        },
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

            result = run_autopilot(
                task="do something",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )
            self.assertEqual(result.status, "done")

            items = [x for x in iter_jsonl(result.evidence_log_path) if isinstance(x, dict) and x.get("kind") == "state_corrupt"]
            self.assertTrue(items)
            rec = items[0]
            details = rec.get("items") if isinstance(rec.get("items"), list) else []
            self.assertTrue(any(isinstance(it, dict) and it.get("label") == "overlay" for it in details))

    def test_run_does_not_auto_derive_values_claims(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            # Simulate a prior values_set event existing (e.g., from `mi init`) without derived claims.
            values_text = "Prefer minimal questions; avoid unnecessary prompts."
            rec = write_values_set_event(
                home_dir=Path(home),
                values_text=values_text,
                compiled_values={},
                notes="test",
            )
            ev_id = str(rec.get("event_id") or "").strip()
            self.assertTrue(ev_id.startswith("ev_"))

            fake_hands = _FakeHands([_mk_result(thread_id="t_vals", last_message="All done.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {
                            "facts": ["ran ls"],
                            "actions": [{"kind": "command", "detail": "ls"}],
                            "results": ["ok"],
                            "unknowns": [],
                            "risk_signals": [],
                        },
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

            result = run_autopilot(
                task="do something",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )
            self.assertEqual(result.status, "done")

            # Global ledger should NOT get a duplicate values_set event.
            gp = GlobalPaths(home_dir=Path(home))
            values_set = [x for x in iter_jsonl(gp.global_evidence_log_path) if isinstance(x, dict) and x.get("kind") == "values_set"]
            self.assertEqual(len(values_set), 1)
            self.assertEqual(str(values_set[0].get("event_id") or "").strip(), ev_id)

            # Project EvidenceLog should NOT record a values_claim_patch (values are only derived via `mi values set` / `mi init`).
            found = any(isinstance(obj, dict) and obj.get("kind") == "values_claim_patch" for obj in iter_jsonl(result.evidence_log_path))
            self.assertFalse(found)

            # Global Thought DB should not gain any derived values:base claims during `mi run`.
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            v = tdb.load_view(scope="global")
            has_values_base = False
            for c in v.iter_claims(include_inactive=True, include_aliases=True):
                if not isinstance(c, dict):
                    continue
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                tagset = {str(x).strip() for x in tags if str(x).strip()}
                if VALUES_BASE_TAG in tagset:
                    has_values_base = True
                    break
            self.assertFalse(has_values_base)

    def test_checkpoint_materializes_thoughtdb_nodes_without_extra_mind_calls(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            # Disable claim mining to keep this test focused (node materialization is deterministic).
            cfg = default_config()
            cfg["runtime"]["thought_db"]["enabled"] = True
            cfg["runtime"]["thought_db"]["auto_mine"] = False
            cfg["runtime"]["thought_db"]["auto_materialize_nodes"] = True
            write_json(config_path(Path(home)), cfg)

            fake_hands = _FakeHands([_mk_result(thread_id="t_nodes", last_message="All done.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {
                            "facts": ["ran ls"],
                            "actions": [{"kind": "command", "detail": "ls"}],
                            "results": ["ok"],
                            "unknowns": [],
                            "risk_signals": [],
                        },
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
                    "checkpoint_decide.json": [
                        {
                            "should_checkpoint": True,
                            "checkpoint_kind": "done",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "checkpoint for node materialization",
                        }
                    ],
                }
            )

            result = run_autopilot(
                task="do something",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )
            self.assertEqual(result.status, "done")

            # EvidenceLog should include node_materialized.
            found = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "node_materialized":
                        self.assertTrue(bool(obj.get("ok", False)))
                        wn = obj.get("written_nodes") if isinstance(obj.get("written_nodes"), list) else []
                        self.assertTrue(len(wn) >= 1)
                        found = True
                        break
            self.assertTrue(found)

            # Thought DB nodes store should contain node records.
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            v = tdb.load_view(scope="project")
            self.assertTrue(any(isinstance(n, dict) and n.get("kind") == "node" for n in v.nodes_by_id.values()))

    def test_nodes_only_mode_still_checkpoints_and_materializes_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            # Nodes-only: disable all checkpoint mining but keep deterministic node materialization on.
            cfg = default_config()
            cfg["runtime"]["workflows"]["auto_mine"] = False
            cfg["runtime"]["preference_mining"]["auto_mine"] = False
            cfg["runtime"]["thought_db"]["enabled"] = True
            cfg["runtime"]["thought_db"]["auto_mine"] = False
            cfg["runtime"]["thought_db"]["auto_materialize_nodes"] = True
            write_json(config_path(Path(home)), cfg)

            fake_hands = _FakeHands([_mk_result(thread_id="t_nodes_only", last_message="All done.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {
                            "facts": ["ran ls"],
                            "actions": [{"kind": "command", "detail": "ls"}],
                            "results": ["ok"],
                            "unknowns": [],
                            "risk_signals": [],
                        },
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
                    "checkpoint_decide.json": [
                        {
                            "should_checkpoint": True,
                            "checkpoint_kind": "done",
                            # Even if the model wants to mine, MI must respect auto_mine=false.
                            "should_mine_workflow": True,
                            "should_mine_preferences": True,
                            "confidence": 0.9,
                            "notes": "checkpoint for node materialization (nodes-only)",
                        }
                    ],
                }
            )

            result = run_autopilot(
                task="do something",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )
            self.assertEqual(result.status, "done")
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "decide_next.json", "checkpoint_decide.json"])

            kinds = set()
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.add(obj["kind"])

            self.assertIn("checkpoint", kinds)
            self.assertIn("snapshot", kinds)
            self.assertIn("node_materialized", kinds)
            self.assertNotIn("workflow_suggestion", kinds)
            self.assertNotIn("preference_mining", kinds)
            self.assertNotIn("claim_mining", kinds)

    def test_recall_prefers_current_project_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            cur_pid = pp.project_id

            # Force a single-item recall so ordering is observable.
            cfg = default_config()
            cfg["runtime"]["cross_project_recall"]["enabled"] = True
            cfg["runtime"]["cross_project_recall"]["top_k"] = 1
            cfg["runtime"]["cross_project_recall"]["exclude_current_project"] = False
            cfg["runtime"]["cross_project_recall"]["prefer_current_project"] = True
            cfg["runtime"]["cross_project_recall"]["triggers"]["run_start"] = True
            write_json(config_path(Path(home)), cfg)

            mem = MemoryService(Path(home))
            mem.upsert_items(
                [
                    MemoryItem(
                        item_id=f"snapshot:project:{cur_pid}:s1",
                        kind="snapshot",
                        scope="project",
                        project_id=cur_pid,
                        ts="2020-01-01T00:00:00Z",
                        title="alpha",
                        body="alpha",
                        tags=["snapshot"],
                        source_refs=[],
                    ),
                    MemoryItem(
                        item_id="snapshot:project:other:s2",
                        kind="snapshot",
                        scope="project",
                        project_id="other",
                        ts="2020-01-01T00:00:00Z",
                        title="alpha",
                        body="alpha",
                        tags=["snapshot"],
                        source_refs=[],
                    ),
                ]
            )

            fake_hands = _FakeHands([_mk_result(thread_id="t_pref_order", last_message="All done.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": ["done"], "unknowns": [], "risk_signals": []},
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

            result = run_autopilot(
                task="alpha",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )
            self.assertEqual(result.status, "done")

            found = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") != "cross_project_recall" or obj.get("reason") != "run_start":
                        continue
                    items = obj.get("items") if isinstance(obj.get("items"), list) else []
                    self.assertEqual(len(items), 1)
                    self.assertEqual(str(items[0].get("project_id") or ""), cur_pid)
                    found = True
                    break
            self.assertTrue(found)

    def test_cross_project_recall_run_start_writes_evidence_event(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            # Seed the cross-project memory index with an item that should match the run_start query (task text).
            mem = MemoryService(Path(home))
            mem.upsert_items(
                [
                    MemoryItem(
                        item_id="snapshot:project:other:1",
                        kind="snapshot",
                        scope="project",
                        project_id="other",
                        ts="2020-01-01T00:00:00Z",
                        title="hello world",
                        body="hello world",
                        tags=["snapshot"],
                        source_refs=[],
                    )
                ]
            )

            fake_hands = _FakeHands([_mk_result(thread_id="t_recall_start", last_message="All done.", command="ls")])
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
                        },
                    ],
                }
            )

            result = run_autopilot(
                task="hello world",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )

            self.assertEqual(result.status, "done")

            found = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") != "cross_project_recall":
                        continue
                    if obj.get("reason") != "run_start":
                        continue
                    self.assertEqual(obj.get("batch_id"), "b0.recall")
                    self.assertTrue(str(obj.get("event_id") or "").strip())
                    self.assertTrue(isinstance(obj.get("items"), list) and obj.get("items"))
                    self.assertEqual(str(obj.get("query_raw") or "").strip(), "hello world")
                    self.assertEqual(str(obj.get("query_compact") or "").strip(), "hello world")
                    toks = obj.get("tokens_used") if isinstance(obj.get("tokens_used"), list) else []
                    self.assertTrue("hello" in [str(x) for x in toks] and "world" in [str(x) for x in toks])
                    found = True
                    break
            self.assertTrue(found)

    def test_cross_project_recall_risk_signal_writes_evidence_event(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            # Avoid interactive prompting in this test.
            cfg = default_config()
            cfg["runtime"]["violation_response"]["prompt_user_on_high_risk"] = False
            write_json(config_path(Path(home)), cfg)

            # Seed a snapshot that should match the risk_signal query ("push: git push origin main").
            mem = MemoryService(Path(home))
            mem.upsert_items(
                [
                    MemoryItem(
                        item_id="snapshot:project:other:push1",
                        kind="snapshot",
                        scope="project",
                        project_id="other",
                        ts="2020-01-01T00:00:00Z",
                        title="git push origin main",
                        body="git push origin main",
                        tags=["snapshot", "push"],
                        source_refs=[],
                    )
                ]
            )

            fake_hands = _FakeHands([_mk_result(thread_id="t_recall_risk", last_message="All done.", command="git push origin main")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                    ],
                    "risk_judge.json": [
                        {"category": "push", "severity": "high", "should_ask_user": False, "mitigation": [], "learned_changes": []},
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
                    "checkpoint_decide.json": [
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
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

            found = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") != "cross_project_recall":
                        continue
                    if obj.get("reason") != "risk_signal":
                        continue
                    self.assertEqual(obj.get("batch_id"), "b0.risk_recall")
                    self.assertTrue(str(obj.get("event_id") or "").strip())
                    self.assertTrue(isinstance(obj.get("items"), list) and obj.get("items"))
                    found = True
                    break
            self.assertTrue(found)

        def test_before_ask_user_recall_retries_auto_answer_and_avoids_user_prompt(self) -> None:
            with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
                # Seed a memory item that should match the "API key" question, so the retry can "answer".
                mem = MemoryService(Path(home))
                mem.upsert_items(
                    [
                        MemoryItem(
                            item_id="snapshot:project:other:apikey1",
                            kind="snapshot",
                        scope="project",
                        project_id="other",
                        ts="2020-01-01T00:00:00Z",
                        title="api key",
                        body="api key",
                        tags=["snapshot"],
                        source_refs=[],
                    )
                ]
            )

            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t_autoanswer_retry", last_message="Need API key?"),
                    _mk_result(thread_id="t_autoanswer_retry", last_message="All done."),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": [], "actions": [], "results": ["done"], "unknowns": [], "risk_signals": []},
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
                    "auto_answer_to_codex.json": [
                        {
                            "should_answer": False,
                            "confidence": 0.2,
                            "codex_answer_input": "",
                            "needs_user_input": True,
                            "ask_user_question": "API key?",
                            "unanswered_questions": ["API key?"],
                            "notes": "need key",
                        },
                        {
                            "should_answer": True,
                            "confidence": 0.9,
                            "codex_answer_input": "Set API_KEY=abc",
                            "needs_user_input": False,
                            "ask_user_question": "",
                            "unanswered_questions": [],
                            "notes": "answered after recall",
                        },
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

            old_stdin = sys.stdin
            old_stderr = sys.stderr
            sys.stdin = io.StringIO("")  # If MI prompts, it will receive an empty answer -> blocked.
            sys.stderr = io.StringIO()
            try:
                result = run_autopilot(
                    task="x",
                    project_root=project_root,
                    home_dir=home,
                    max_batches=3,
                    hands_exec=fake_hands.exec,
                    hands_resume=fake_hands.resume,
                    llm=fake_llm,
                )
            finally:
                sys.stdin = old_stdin
                sys.stderr = old_stderr

            self.assertEqual(result.status, "done")
            self.assertEqual(fake_hands.exec_calls, 1)
            self.assertEqual(fake_hands.resume_calls, 1)
            self.assertEqual(
                fake_llm.calls,
                [
                    "extract_evidence.json",
                    "plan_min_checks.json",
                    "auto_answer_to_codex.json",
                    "auto_answer_to_codex.json",
                    "checkpoint_decide.json",
                    "extract_evidence.json",
                    "decide_next.json",
                    "checkpoint_decide.json",
                ],
            )

            # Verify MI performed a recall before asking the user, and that the retry auto-answer succeeded.
            idx_recall = -1
            idx_aa_first = -1
            idx_aa_retry = -1
            has_user_input = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") == "user_input":
                        has_user_input = True
                    if obj.get("kind") == "cross_project_recall" and obj.get("reason") == "before_ask_user":
                        idx_recall = i
                        self.assertEqual(obj.get("batch_id"), "b0.before_user_recall")
                    if obj.get("kind") == "auto_answer":
                        if obj.get("batch_id") == "b0":
                            idx_aa_first = i
                            aa = obj.get("auto_answer") if isinstance(obj.get("auto_answer"), dict) else {}
                            self.assertTrue(bool(aa.get("needs_user_input", False)))
                        if obj.get("batch_id") == "b0.after_recall":
                            idx_aa_retry = i
                            aa2 = obj.get("auto_answer") if isinstance(obj.get("auto_answer"), dict) else {}
                            self.assertTrue(bool(aa2.get("should_answer", False)))

            self.assertFalse(has_user_input)
            self.assertTrue(idx_aa_first >= 0 and idx_recall >= 0 and idx_aa_retry >= 0)
            self.assertTrue(idx_aa_first < idx_recall < idx_aa_retry)

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
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "decide_next.json", "checkpoint_decide.json"])

            kinds = set()
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.add(obj["kind"])
            self.assertIn("hands_input", kinds)
            self.assertIn("check_plan", kinds)
            self.assertIn("decide_next", kinds)

    def test_run_can_auto_why_trace_on_end_when_opted_in(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            cfg = default_config()
            cfg.setdefault("runtime", {})
            cfg["runtime"].setdefault("thought_db", {})
            cfg["runtime"]["thought_db"].setdefault("why_trace", {})
            cfg["runtime"]["thought_db"]["why_trace"]["auto_on_run_end"] = True
            write_json(config_path(Path(home)), cfg)

            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t_why", last_message="All done.", command="ls"),
                ]
            )
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
                    "why_trace.json": [
                        {
                            "status": "insufficient",
                            "confidence": 0.2,
                            "chosen_claim_ids": [],
                            "explanation": "Not enough context.",
                            "notes": "auto run_end",
                        }
                    ],
                }
            )

            result = run_autopilot(
                task="smoke task",
                project_root=project_root,
                home_dir=home,
                max_batches=2,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )

            self.assertEqual(result.status, "done")
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "decide_next.json", "checkpoint_decide.json", "why_trace.json"])

            found = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "why_trace":
                        found = True
                        break
            self.assertTrue(found)

    def test_plan_min_checks_prompts_for_testless_strategy_and_replans(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t_tls", last_message="All done.", command="ls"),
                    _mk_result(thread_id="t_tls", last_message="Checks done.", command="python -m compileall -q ."),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ran ls"], "actions": [], "results": ["ok"], "unknowns": ["no tests"], "risk_signals": []},
                        {"facts": ["ran compileall"], "actions": [], "results": ["ok"], "unknowns": [], "risk_signals": []},
                    ],
                    "plan_min_checks.json": [
                        {
                            "should_run_checks": False,
                            "needs_testless_strategy": True,
                            "testless_strategy_question": "Choose a testless verification strategy:",
                            "check_goals": [],
                            "commands_hints": [],
                            "codex_check_input": "",
                            "notes": "need strategy",
                        },
                        {
                            "should_run_checks": True,
                            "needs_testless_strategy": False,
                            "testless_strategy_question": "",
                            "check_goals": ["basic verification"],
                            "commands_hints": [],
                            "codex_check_input": "Run: python -m compileall -q .",
                            "notes": "ok",
                        },
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
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                    ],
                }
            )

            old_stdin = sys.stdin
            old_stderr = sys.stderr
            sys.stdin = io.StringIO("compileall + import smoke\n")
            sys.stderr = io.StringIO()
            try:
                result = run_autopilot(
                    task="smoke task",
                    project_root=project_root,
                    home_dir=home,
                    max_batches=3,
                    hands_exec=fake_hands.exec,
                    hands_resume=fake_hands.resume,
                    llm=fake_llm,
                )
            finally:
                sys.stdin = old_stdin
                sys.stderr = old_stderr

            self.assertEqual(result.status, "done")
            self.assertEqual(
                fake_llm.calls,
                [
                    "extract_evidence.json",
                    "plan_min_checks.json",
                    "plan_min_checks.json",
                    "checkpoint_decide.json",
                    "extract_evidence.json",
                    "decide_next.json",
                    "checkpoint_decide.json",
                ],
            )

            found_user_input = False
            found_after_testless = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") == "user_input":
                        found_user_input = True
                    if obj.get("kind") == "check_plan" and obj.get("batch_id") == "b0.after_testless":
                        found_after_testless = True
            self.assertTrue(found_user_input)
            self.assertTrue(found_after_testless)

    def test_plan_min_checks_after_tls_claim_failure_does_not_prompt_user(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            # Seed a canonical TLS claim so MI should not prompt the user.
            tdb.append_claim_create(
                claim_type="preference",
                text="When this project has no tests, use this verification strategy: smoke",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[TESTLESS_STRATEGY_TAG],
                source_event_ids=["ev_seed_tls_0001"],
                confidence=1.0,
                notes="seed tls",
            )

            fake_hands = _FakeHands([_mk_result(thread_id="t_tls_fail", last_message="Working.", command="ls")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ran ls"], "actions": [], "results": ["ok"], "unknowns": ["need verify"], "risk_signals": []},
                    ],
                    "plan_min_checks.json": [
                        {
                            "should_run_checks": False,
                            "needs_testless_strategy": True,
                            "testless_strategy_question": "Choose a testless verification strategy:",
                            "check_goals": [],
                            "commands_hints": [],
                            "codex_check_input": "",
                            "notes": "need tls",
                        },
                        RuntimeError("boom"),
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

            old_stdin = sys.stdin
            old_stderr = sys.stderr
            sys.stdin = io.StringIO("\n")
            sys.stderr = io.StringIO()
            try:
                result = run_autopilot(
                    task="smoke task",
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

            self.assertEqual(result.status, "done")

            found_user_input = False
            found_after_tls_claim = False
            needs_flag = None
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") == "user_input":
                        found_user_input = True
                    if obj.get("kind") == "check_plan" and obj.get("batch_id") == "b0.after_tls_claim":
                        found_after_tls_claim = True
                        checks = obj.get("checks") if isinstance(obj.get("checks"), dict) else {}
                        needs_flag = checks.get("needs_testless_strategy")

            self.assertFalse(found_user_input)
            self.assertTrue(found_after_tls_claim)
            self.assertEqual(needs_flag, False)

    def test_decide_next_overlay_update_sets_testless_strategy_claim_and_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            fake_hands = _FakeHands([_mk_result(thread_id="t_tls_overlay", last_message="All done.", command="ls")])
            strategy = "compileall + import smoke"

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
                            "next_codex_input": "",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": {"strategy": strategy, "rationale": "test"}},
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

            result = run_autopilot(
                task="smoke task",
                project_root=project_root,
                home_dir=home,
                max_batches=1,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )
            self.assertEqual(result.status, "done")

            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            overlay = json.loads(pp.overlay_path.read_text(encoding="utf-8"))
            tls = overlay.get("testless_verification_strategy") if isinstance(overlay, dict) else {}
            self.assertTrue(bool(tls.get("chosen_once", False)))
            claim_id = str(tls.get("claim_id") or "").strip()
            self.assertTrue(claim_id.startswith("cl_"))

            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            v = tdb.load_view(scope="project")
            found_claim: dict | None = None
            for c in v.iter_claims(include_inactive=True, include_aliases=True):
                if str(c.get("claim_id") or "").strip() == claim_id:
                    found_claim = c
                    break
            self.assertTrue(isinstance(found_claim, dict))
            tags = found_claim.get("tags") if isinstance(found_claim.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            self.assertIn(TESTLESS_STRATEGY_TAG, tagset)
            self.assertIn(strategy, str(found_claim.get("text") or ""))

    def test_decide_next_after_user_overlay_update_sets_testless_strategy_claim_and_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            fake_hands = _FakeHands([_mk_result(thread_id="t_tls_after_user", last_message="Working.", command="ls")])
            strategy = "compileall + import smoke"

            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ran ls"], "actions": [], "results": ["ok"], "unknowns": [], "risk_signals": []},
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "ask_user",
                            "status": "not_done",
                            "confidence": 0.7,
                            "next_codex_input": "",
                            "ask_user_question": "Need your input:",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "ask user",
                        },
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_codex_input": "",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": {"strategy": strategy, "rationale": "test"}},
                            "notes": "done",
                        },
                    ],
                    "auto_answer_to_codex.json": [
                        {
                            "should_answer": False,
                            "confidence": 0.2,
                            "codex_answer_input": "",
                            "needs_user_input": True,
                            "ask_user_question": "Need your input:",
                            "unanswered_questions": [],
                            "notes": "need user",
                        },
                        {
                            "should_answer": False,
                            "confidence": 0.2,
                            "codex_answer_input": "",
                            "needs_user_input": True,
                            "ask_user_question": "Need your input:",
                            "unanswered_questions": [],
                            "notes": "need user",
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

            old_stdin = sys.stdin
            old_stderr = sys.stderr
            sys.stdin = io.StringIO("ok\n")
            sys.stderr = io.StringIO()
            try:
                result = run_autopilot(
                    task="smoke task",
                    project_root=project_root,
                    home_dir=home,
                    max_batches=2,
                    hands_exec=fake_hands.exec,
                    hands_resume=fake_hands.resume,
                    llm=fake_llm,
                )
            finally:
                sys.stdin = old_stdin
                sys.stderr = old_stderr

            self.assertEqual(result.status, "done")
            self.assertEqual(
                fake_llm.calls,
                [
                    "extract_evidence.json",
                    "decide_next.json",
                    "auto_answer_to_codex.json",
                    "auto_answer_to_codex.json",
                    "decide_next.json",
                    "checkpoint_decide.json",
                ],
            )

            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            overlay = json.loads(pp.overlay_path.read_text(encoding="utf-8"))
            tls = overlay.get("testless_verification_strategy") if isinstance(overlay, dict) else {}
            self.assertTrue(bool(tls.get("chosen_once", False)))
            claim_id = str(tls.get("claim_id") or "").strip()
            self.assertTrue(claim_id.startswith("cl_"))

            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            v = tdb.load_view(scope="project")
            found_claim2: dict | None = None
            for c in v.iter_claims(include_inactive=True, include_aliases=True):
                if str(c.get("claim_id") or "").strip() == claim_id:
                    found_claim2 = c
                    break
            self.assertTrue(isinstance(found_claim2, dict))
            tags = found_claim2.get("tags") if isinstance(found_claim2.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            self.assertIn(TESTLESS_STRATEGY_TAG, tagset)
            self.assertIn(strategy, str(found_claim2.get("text") or ""))

    def test_loop_break_run_checks_then_continue_can_prompt_for_testless_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t_lb", last_message="Still working."),
                    _mk_result(thread_id="t_lb", last_message="Still working."),
                    _mk_result(thread_id="t_lb", last_message="Still working."),
                    _mk_result(thread_id="t_lb", last_message="Checks done.", command="python -m compileall -q ."),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": ["ran compileall"], "actions": [], "results": ["ok"], "unknowns": [], "risk_signals": []},
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
                    "loop_break.json": [
                        {
                            "action": "run_checks_then_continue",
                            "confidence": 0.8,
                            "rewritten_next_input": "",
                            "check_intent": "force progress via checks",
                            "ask_user_question": "",
                            "notes": "Run checks to break the loop.",
                        }
                    ],
                    "plan_min_checks.json": [
                        {
                            "should_run_checks": False,
                            "needs_testless_strategy": True,
                            "testless_strategy_question": "Choose a testless verification strategy:",
                            "check_goals": [],
                            "commands_hints": [],
                            "codex_check_input": "",
                            "notes": "need strategy",
                        },
                        {
                            "should_run_checks": True,
                            "needs_testless_strategy": False,
                            "testless_strategy_question": "",
                            "check_goals": ["basic verification"],
                            "commands_hints": [],
                            "codex_check_input": "Run: python -m compileall -q .",
                            "notes": "ok",
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
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                    ],
                }
            )

            old_stdin = sys.stdin
            old_stderr = sys.stderr
            sys.stdin = io.StringIO("compileall + import smoke\n")
            sys.stderr = io.StringIO()
            try:
                result = run_autopilot(
                    task="start",
                    project_root=project_root,
                    home_dir=home,
                    max_batches=8,
                    hands_exec=fake_hands.exec,
                    hands_resume=fake_hands.resume,
                    llm=fake_llm,
                )
            finally:
                sys.stdin = old_stdin
                sys.stderr = old_stderr

            self.assertEqual(result.status, "done")
            self.assertEqual(
                fake_llm.calls,
                [
                    "extract_evidence.json",
                    "decide_next.json",
                    "checkpoint_decide.json",
                    "extract_evidence.json",
                    "decide_next.json",
                    "checkpoint_decide.json",
                    "extract_evidence.json",
                    "decide_next.json",
                    "loop_break.json",
                    "plan_min_checks.json",
                    "plan_min_checks.json",
                    "checkpoint_decide.json",
                    "extract_evidence.json",
                    "decide_next.json",
                    "checkpoint_decide.json",
                ],
            )

            found_user_input = False
            found_loop_break = False
            found_after_testless = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") == "user_input":
                        found_user_input = True
                    if obj.get("kind") == "loop_break":
                        found_loop_break = True
                    if obj.get("kind") == "check_plan" and obj.get("batch_id") == "b2.loop_break_after_testless":
                        found_after_testless = True
            self.assertTrue(found_loop_break)
            self.assertTrue(found_user_input)
            self.assertTrue(found_after_testless)

    def test_loop_guard_blocks_when_repeating_and_ask_when_uncertain_false(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            ensure_operational_defaults_claims_current(
                home_dir=Path(home),
                tdb=tdb,
                desired_defaults={"refactor_intent": "behavior_preserving", "ask_when_uncertain": False},
                mode="sync",
            )

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
                    "loop_break.json": [
                        {
                            "action": "stop_blocked",
                            "confidence": 0.7,
                            "rewritten_next_input": "",
                            "check_intent": "",
                            "ask_user_question": "",
                            "notes": "Repeated loop; stop to avoid thrash.",
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
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
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

    def test_loop_break_rewrites_next_input_and_converges(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            ensure_operational_defaults_claims_current(
                home_dir=Path(home),
                tdb=tdb,
                desired_defaults={"refactor_intent": "behavior_preserving", "ask_when_uncertain": False},
                mode="sync",
            )

            fake_hands = _FakeHands(
                [
                    _mk_result(thread_id="t_lb", last_message="Still working."),
                    _mk_result(thread_id="t_lb", last_message="Still working."),
                    _mk_result(thread_id="t_lb", last_message="Still working."),
                    _mk_result(thread_id="t_lb", last_message="All done."),
                ]
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                        {"facts": ["done"], "actions": [], "results": ["ok"], "unknowns": [], "risk_signals": []},
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
                    "loop_break.json": [
                        {
                            "action": "rewrite_next_input",
                            "confidence": 0.8,
                            "rewritten_next_input": "Stop repeating. Summarize what changed since last batch and run a minimal check if available.",
                            "check_intent": "",
                            "ask_user_question": "",
                            "notes": "Rewrite to force progress.",
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
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                        {
                            "should_checkpoint": False,
                            "checkpoint_kind": "none",
                            "should_mine_workflow": False,
                            "should_mine_preferences": False,
                            "confidence": 0.9,
                            "notes": "no",
                        },
                    ],
                }
            )

            result = run_autopilot(
                task="start",
                project_root=project_root,
                home_dir=home,
                max_batches=8,
                hands_exec=fake_hands.exec,
                hands_resume=fake_hands.resume,
                llm=fake_llm,
            )

            self.assertEqual(result.status, "done")
            found_loop_break = False
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind") == "loop_break":
                        found_loop_break = True
                        break
            self.assertTrue(found_loop_break)

    def test_risk_prompt_triggers_only_for_configured_severities(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            cfg = default_config()
            cfg["runtime"]["violation_response"]["prompt_user_on_high_risk"] = True
            cfg["runtime"]["violation_response"]["prompt_user_risk_severities"] = ["high"]
            cfg["runtime"]["violation_response"]["prompt_user_respect_should_ask_user"] = True
            write_json(config_path(Path(home)), cfg)

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
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "risk_judge.json", "checkpoint_decide.json"])

            kinds = set()
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.add(obj["kind"])
            self.assertIn("risk_event", kinds)

    def test_risk_prompt_skips_when_severity_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            cfg = default_config()
            cfg["runtime"]["violation_response"]["prompt_user_on_high_risk"] = True
            cfg["runtime"]["violation_response"]["prompt_user_risk_severities"] = ["high", "critical"]
            cfg["runtime"]["violation_response"]["prompt_user_respect_should_ask_user"] = True
            write_json(config_path(Path(home)), cfg)

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
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "risk_judge.json", "decide_next.json", "checkpoint_decide.json"])

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
            self.assertEqual(
                fake_llm.calls,
                ["extract_evidence.json", "plan_min_checks.json", "decide_next.json", "checkpoint_decide.json"],
            )

            kinds = []
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.append(obj["kind"])
            self.assertIn("mind_error", kinds)

    def test_mind_error_decide_next_blocks_when_ask_when_uncertain_false(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            ensure_operational_defaults_claims_current(
                home_dir=Path(home),
                tdb=tdb,
                desired_defaults={"refactor_intent": "behavior_preserving", "ask_when_uncertain": False},
                mode="sync",
            )

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
            cfg = default_config()
            cfg["runtime"]["violation_response"]["prompt_user_on_high_risk"] = False
            write_json(config_path(Path(home)), cfg)

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
            self.assertEqual(
                fake_llm.calls,
                ["extract_evidence.json", "risk_judge.json", "decide_next.json", "checkpoint_decide.json"],
            )

            kinds = set()
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("kind"):
                        kinds.add(obj["kind"])
            self.assertIn("mind_error", kinds)
            self.assertIn("risk_event", kinds)

    def test_mind_circuit_opens_and_skips_further_mind_calls(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            ensure_operational_defaults_claims_current(
                home_dir=Path(home),
                tdb=tdb,
                desired_defaults={"refactor_intent": "behavior_preserving", "ask_when_uncertain": False},
                mode="sync",
            )

            fake_hands = _FakeHands([_mk_result(thread_id="t8", last_message="Should I proceed?")])
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        MindCallError(
                            "down",
                            schema_filename="extract_evidence.json",
                            tag="extract_b0",
                            transcript_path=Path("mind_extract.jsonl"),
                        )
                    ],
                    "plan_min_checks.json": [
                        MindCallError(
                            "down",
                            schema_filename="plan_min_checks.json",
                            tag="checks_b0",
                            transcript_path=Path("mind_checks.jsonl"),
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
            # Circuit should prevent further Mind calls (auto_answer/decide_next).
            self.assertEqual(fake_llm.calls, ["extract_evidence.json", "plan_min_checks.json"])

            mind_error_n = 0
            mind_circuit_n = 0
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") == "mind_error":
                        mind_error_n += 1
                    if obj.get("kind") == "mind_circuit":
                        mind_circuit_n += 1

            self.assertEqual(mind_error_n, 2)
            self.assertEqual(mind_circuit_n, 1)


if __name__ == "__main__":
    unittest.main()
