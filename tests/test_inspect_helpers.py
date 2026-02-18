from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mi.runtime.inspect import (
    classify_evidence_record,
    load_last_batch_bundle,
    summarize_evidence_record,
    tail_json_objects,
    tail_raw_lines,
)
from mi.runtime.transcript import last_agent_message_from_transcript


class TestInspectHelpers(unittest.TestCase):
    def test_tail_raw_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "evidence.jsonl"
            p.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
            self.assertEqual(tail_raw_lines(p, 2), ["d", "e"])
            self.assertEqual(tail_raw_lines(p, 0), [])

    def test_tail_json_objects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "evidence.jsonl"
            p.write_text('{"a":1}\nnotjson\n{"b":2}\n', encoding="utf-8")
            objs = tail_json_objects(p, 3)
            self.assertEqual(len(objs), 2)
            self.assertEqual(objs[0]["a"], 1)
            self.assertEqual(objs[1]["b"], 2)

    def test_load_last_batch_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "evidence.jsonl"
            lines = [
                json.dumps(
                    {
                        "kind": "state_corrupt",
                        "batch_id": "b0.state_recovery",
                        "ts": "x",
                        "thread_id": "t",
                        "items": [{"label": "overlay", "path": "/tmp/overlay.json", "error": "JSONDecodeError"}],
                    }
                ),
                json.dumps({"kind": "codex_input", "batch_id": "b0", "thread_id": "t", "input": "hi", "transcript_path": "t0"}),
                json.dumps(
                    {
                        "batch_id": "b0",
                        "ts": "x",
                        "thread_id": "t",
                        "codex_transcript_ref": "t0",
                        "facts": ["f0"],
                        "actions": [],
                        "results": [],
                        "unknowns": [],
                        "risk_signals": [],
                    }
                ),
                json.dumps({"kind": "codex_input", "batch_id": "b1", "thread_id": "t", "input": "next", "transcript_path": "t1"}),
                json.dumps(
                    {
                        "kind": "check_plan",
                        "batch_id": "b1",
                        "thread_id": "t",
                        "mind_transcript_ref": "m_checks_1",
                        "checks": {"should_run_checks": False},
                    }
                ),
                json.dumps(
                    {
                        "kind": "check_plan",
                        "batch_id": "b1.after_testless",
                        "thread_id": "t",
                        "mind_transcript_ref": "m_checks_2",
                        "checks": {"should_run_checks": True},
                    }
                ),
                json.dumps(
                    {
                        "kind": "auto_answer",
                        "batch_id": "b1.from_decide",
                        "thread_id": "t",
                        "mind_transcript_ref": "m_autoanswer",
                        "auto_answer": {"should_answer": False, "needs_user_input": False},
                    }
                ),
                json.dumps(
                    {
                        "kind": "decide_next",
                        "batch_id": "b1",
                        "thread_id": "t",
                        "phase": "initial",
                        "next_action": "stop",
                        "status": "done",
                        "confidence": 0.9,
                        "notes": "done",
                        "ask_user_question": "",
                        "next_codex_input": "",
                        "mind_transcript_ref": "m_decide",
                        "decision": {"next_action": "stop", "status": "done", "confidence": 0.9, "notes": "done"},
                    }
                ),
                json.dumps(
                    {
                        "kind": "why_trace",
                        "batch_id": "b1.why_trace",
                        "thread_id": "t",
                        "mind_transcript_ref": "m_why",
                        "output": {"status": "ok", "confidence": 0.8, "chosen_claim_ids": ["c1"]},
                        "written_edge_ids": ["e1"],
                    }
                ),
                json.dumps(
                    {
                        "kind": "learn_suggested",
                        "id": "ls_123",
                        "batch_id": "b1",
                        "ts": "x",
                        "thread_id": "t",
                        "source": "decide_next",
                        "auto_learn": False,
                        "mind_transcript_ref": "m_decide",
                        "learned_changes": [{"scope": "project", "text": "x", "rationale": "y"}],
                        "applied_entry_ids": [],
                    }
                ),
                json.dumps(
                    {
                        "kind": "learn_applied",
                        "ts": "x",
                        "suggestion_id": "ls_123",
                        "batch_id": "b1",
                        "thread_id": "t",
                        "applied_entry_ids": ["lc_1"],
                    }
                ),
                json.dumps(
                    {
                        "kind": "loop_break",
                        "batch_id": "b1",
                        "thread_id": "t",
                        "pattern": "aaa",
                        "state": "ok",
                        "mind_transcript_ref": "m_loopbreak",
                        "output": {"action": "rewrite_next_input"},
                    }
                ),
                json.dumps(
                    {
                        "batch_id": "b1",
                        "ts": "x",
                        "thread_id": "t",
                        "codex_transcript_ref": "t1",
                        "mind_transcript_ref": "m_extract",
                        "facts": ["f1"],
                        "actions": [],
                        "results": ["r1"],
                        "unknowns": ["u1"],
                        "risk_signals": [],
                    }
                ),
            ]
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")

            bundle = load_last_batch_bundle(p)
            self.assertEqual(bundle["batch_id"], "b1")
            self.assertIsNotNone(bundle["codex_input"])
            self.assertIsNotNone(bundle["evidence_item"])
            self.assertIsNotNone(bundle["check_plan"])
            self.assertIsNotNone(bundle["decide_next"])
            self.assertIsNotNone(bundle["why_trace"])
            self.assertIsInstance(bundle.get("why_traces"), list)
            self.assertTrue(any(isinstance(x, dict) and x.get("kind") == "why_trace" for x in (bundle.get("why_traces") or [])))
            self.assertIsNotNone(bundle.get("state_corrupt_recent"))
            scr = bundle.get("state_corrupt_recent")
            self.assertIsInstance(scr, dict)
            self.assertTrue(isinstance(scr.get("items"), list) and len(scr.get("items")) > 0)
            ls = bundle.get("learn_suggested")
            self.assertIsInstance(ls, list)
            self.assertTrue(any(isinstance(x, dict) and x.get("id") == "ls_123" for x in ls))
            la = bundle.get("learn_applied")
            self.assertIsInstance(la, list)
            self.assertTrue(any(isinstance(x, dict) and x.get("suggestion_id") == "ls_123" for x in la))
            mts = bundle.get("mind_transcripts")
            self.assertIsInstance(mts, list)
            refs = {m.get("mind_transcript_ref") for m in mts if isinstance(m, dict)}
            self.assertIn("m_extract", refs)
            self.assertIn("m_checks_1", refs)
            self.assertIn("m_checks_2", refs)
            self.assertIn("m_autoanswer", refs)
            self.assertIn("m_decide", refs)
            self.assertIn("m_why", refs)
            self.assertIn("m_loopbreak", refs)

    def test_classify_and_summarize(self) -> None:
        ev = {"batch_id": "b0", "facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []}
        self.assertEqual(classify_evidence_record(ev), "evidence")
        s = summarize_evidence_record({"kind": "loop_guard", "batch_id": "b0", "pattern": "aaa"})
        self.assertIn("loop_guard", s)
        s2 = summarize_evidence_record({"kind": "loop_break", "batch_id": "b0", "pattern": "aaa", "state": "ok", "output": {"action": "rewrite_next_input"}})
        self.assertIn("loop_break", s2)

    def test_last_agent_message_from_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hands.jsonl"
            rows = [
                {"ts": "t", "stream": "meta", "line": "hdr"},
                {
                    "ts": "t",
                    "stream": "stdout",
                    "line": json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}}),
                },
                {
                    "ts": "t",
                    "stream": "stdout",
                    "line": json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "bye"}}),
                },
            ]
            p.write_text("\n".join([json.dumps(r) for r in rows]) + "\n", encoding="utf-8")
            self.assertEqual(last_agent_message_from_transcript(p), "bye")


if __name__ == "__main__":
    unittest.main()
