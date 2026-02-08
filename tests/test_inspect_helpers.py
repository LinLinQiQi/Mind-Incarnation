import json
import tempfile
import unittest
from pathlib import Path

from mi.inspect import (
    classify_evidence_record,
    load_last_batch_bundle,
    summarize_evidence_record,
    tail_json_objects,
    tail_raw_lines,
)
from mi.transcript import last_agent_message_from_transcript


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
                json.dumps({"kind": "check_plan", "batch_id": "b1", "thread_id": "t", "checks": {"should_run_checks": False}}),
                json.dumps(
                    {
                        "batch_id": "b1",
                        "ts": "x",
                        "thread_id": "t",
                        "codex_transcript_ref": "t1",
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

    def test_classify_and_summarize(self) -> None:
        ev = {"batch_id": "b0", "facts": [], "actions": [], "results": [], "unknowns": [], "risk_signals": []}
        self.assertEqual(classify_evidence_record(ev), "evidence")
        s = summarize_evidence_record({"kind": "loop_guard", "batch_id": "b0", "pattern": "aaa"})
        self.assertIn("loop_guard", s)

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

