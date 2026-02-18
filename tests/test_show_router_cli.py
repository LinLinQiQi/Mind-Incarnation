from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.paths import GlobalPaths, ProjectPaths
from mi.thoughtdb import ThoughtDbStore
from mi.workflows import WorkflowStore


class TestShowRouterCli(unittest.TestCase):
    def test_show_ev_falls_back_to_global(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            gp = GlobalPaths(home_dir=home)
            gp.global_evidence_log_path.parent.mkdir(parents=True, exist_ok=True)
            gp.global_evidence_log_path.write_text(json.dumps({"event_id": "ev_global", "ts": "t", "kind": "values_set"}) + "\n", encoding="utf-8")

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "show", "ev_global", "--cd", str(proj), "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload.get("scope"), "global")
            self.assertEqual(payload.get("event", {}).get("event_id"), "ev_global")

    def test_show_claim_from_project(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=proj)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            cid = tdb.append_claim_create(
                claim_type="preference",
                text="Prefer fewer questions.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["test"],
                source_event_ids=[],
                confidence=1.0,
                notes="test",
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "show", cid, "--cd", str(proj), "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload.get("scope"), "project")
            self.assertEqual(payload.get("claim", {}).get("claim_id"), cid)

    def test_show_workflow_delegates_to_workflow_show(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=proj)
            wf_store = WorkflowStore(pp)
            wid = "wf_test_show"
            wf_store.write(
                {
                    "version": "v1",
                    "id": wid,
                    "name": "Test Workflow",
                    "enabled": True,
                    "trigger": {"mode": "manual", "pattern": ""},
                    "mermaid": "",
                    "steps": [],
                    "source": {"kind": "manual", "reason": "test", "evidence_refs": []},
                    "created_ts": "t",
                    "updated_ts": "t",
                }
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "show", wid, "--cd", str(proj), "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(code, 0)
            obj = json.loads(out)
            self.assertEqual(str(obj.get("id") or ""), wid)
            self.assertEqual(str(obj.get("name") or ""), "Test Workflow")

    def test_show_transcript_path(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td:
            home = Path(td_home)
            tp = Path(td) / "t.jsonl"
            tp.write_text(json.dumps({"ts": "t", "stream": "stdout", "line": "hello"}) + "\n", encoding="utf-8")

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "show", str(tp), "--jsonl"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(code, 0)
            self.assertIn("t.jsonl", out)
            self.assertIn("hello", out)


if __name__ == "__main__":
    unittest.main()

