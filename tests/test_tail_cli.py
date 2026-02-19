from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.paths import GlobalPaths, ProjectPaths
from mi.core.storage import append_jsonl


def _run_cli(argv: list[str]) -> tuple[int, str]:
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        code = mi_main(argv)
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return code, out


class TestTailCli(unittest.TestCase):
    def test_tail_evidence_summary_raw_json_and_global(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)

            append_jsonl(pp.evidence_log_path, {"event_id": "ev_p1", "kind": "hands_input", "input": "x"})
            append_jsonl(pp.evidence_log_path, {"event_id": "ev_p2", "kind": "decide_next", "status": "done", "next_action": "stop"})

            code, out = _run_cli(["--home", str(home), "tail", "--cd", str(project_root), "-n", "2"])
            self.assertEqual(code, 0)
            self.assertIn("hands_input", out)
            self.assertIn("decide_next", out)

            code, out = _run_cli(["--home", str(home), "tail", "--cd", str(project_root), "-n", "2", "--raw"])
            self.assertEqual(code, 0)
            self.assertIn("\"event_id\": \"ev_p1\"", out)
            self.assertIn("\"event_id\": \"ev_p2\"", out)

            code, out = _run_cli(["--home", str(home), "tail", "--cd", str(project_root), "-n", "2", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertIsInstance(payload, list)
            ids = {str(x.get("event_id") or "") for x in payload if isinstance(x, dict)}
            self.assertIn("ev_p1", ids)
            self.assertIn("ev_p2", ids)

            gp = GlobalPaths(home_dir=home)
            append_jsonl(gp.global_evidence_log_path, {"event_id": "ev_g1", "kind": "values_set", "text": "v"})
            code, out = _run_cli(["--home", str(home), "tail", "--global", "-n", "1", "--raw"])
            self.assertEqual(code, 0)
            self.assertIn("\"event_id\": \"ev_g1\"", out)

    def test_tail_hands_and_mind(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            hands_dir = pp.transcripts_dir / "hands"
            mind_dir = pp.transcripts_dir / "mind"
            hands_dir.mkdir(parents=True, exist_ok=True)
            mind_dir.mkdir(parents=True, exist_ok=True)

            (hands_dir / "h.jsonl").write_text(
                json.dumps({"ts": "t1", "stream": "stdout", "line": "hands-line"}) + "\n",
                encoding="utf-8",
            )
            (mind_dir / "m.jsonl").write_text(
                json.dumps({"ts": "t2", "stream": "stdout", "line": "mind-line"}) + "\n",
                encoding="utf-8",
            )

            code, out = _run_cli(["--home", str(home), "tail", "hands", "--cd", str(project_root), "-n", "1"])
            self.assertEqual(code, 0)
            self.assertIn("h.jsonl", out)
            self.assertIn("hands-line", out)

            code, out = _run_cli(["--home", str(home), "tail", "mind", "--cd", str(project_root), "-n", "1", "--jsonl"])
            self.assertEqual(code, 0)
            self.assertIn("m.jsonl", out)
            self.assertIn("mind-line", out)


if __name__ == "__main__":
    unittest.main()
