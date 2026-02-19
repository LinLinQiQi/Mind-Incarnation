from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.paths import ProjectPaths
from mi.core.storage import append_jsonl


class TestLastCliRedact(unittest.TestCase):
    def test_last_redacts_nested_decide_next_and_learn_update(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=proj)
            pp.evidence_log_path.parent.mkdir(parents=True, exist_ok=True)

            append_jsonl(
                pp.evidence_log_path,
                {
                    "kind": "hands_input",
                    "batch_id": "b0",
                    "thread_id": "t",
                    "input": "api_key=sk-test-1234567890",
                    "transcript_path": str(proj / "missing_transcript.jsonl"),
                },
            )
            append_jsonl(
                pp.evidence_log_path,
                {
                    "kind": "decide_next",
                    "batch_id": "b0",
                    "thread_id": "t",
                    "phase": "initial",
                    "next_action": "stop",
                    "status": "done",
                    "confidence": 0.9,
                    "notes": "token=sk-test-1234567890",
                    "ask_user_question": "",
                    "next_hands_input": "",
                    "decision": {
                        "next_action": "stop",
                        "status": "done",
                        "confidence": 0.9,
                        "notes": "api_key=sk-test-1234567890",
                        "ask_user_question": "",
                        "next_hands_input": "",
                    },
                },
            )
            append_jsonl(
                pp.evidence_log_path,
                {
                    "kind": "learn_update",
                    "batch_id": "b0.learn_update",
                    "thread_id": "t",
                    "state": "ok",
                    "output": {
                        "should_apply": True,
                        "min_confidence": 0.9,
                        "patch": {
                            "claims": [{"local_id": "c1", "text": "api_key=sk-test-1234567890", "notes": "token=sk-test-1234567890"}],
                            "edges": [{"notes": "token=sk-test-1234567890"}],
                            "notes": "",
                        },
                        "retract": [
                            {
                                "scope": "project",
                                "claim_id": "cl_1",
                                "rationale": "api_key=sk-test-1234567890",
                                "confidence": 1.0,
                                "source_event_ids": ["ev_x"],
                            }
                        ],
                        "notes": "",
                    },
                    "applied": {"written": [], "written_edges": [], "retracted": [], "retract_skipped": []},
                },
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "last", "--cd", str(proj), "--json", "--redact"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(code, 0)
            payload = json.loads(out)

            self.assertIn("[REDACTED]", str(payload.get("mi_input") or ""))

            dn = payload.get("decide_next")
            self.assertIsInstance(dn, dict)
            self.assertIn("[REDACTED]", str(dn.get("notes") or ""))
            inner = dn.get("decision")
            self.assertIsInstance(inner, dict)
            self.assertIn("[REDACTED]", str(inner.get("notes") or ""))

            lu = payload.get("learn_update")
            self.assertIsInstance(lu, dict)
            out2 = lu.get("output")
            self.assertIsInstance(out2, dict)
            patch = out2.get("patch")
            self.assertIsInstance(patch, dict)
            claims = patch.get("claims")
            self.assertIsInstance(claims, list)
            self.assertTrue(len(claims) >= 1)
            c0 = claims[0]
            self.assertIsInstance(c0, dict)
            self.assertIn("[REDACTED]", str(c0.get("text") or ""))

            retracts = out2.get("retract")
            self.assertIsInstance(retracts, list)
            self.assertTrue(len(retracts) >= 1)
            r0 = retracts[0]
            self.assertIsInstance(r0, dict)
            self.assertIn("[REDACTED]", str(r0.get("rationale") or ""))


if __name__ == "__main__":
    unittest.main()

