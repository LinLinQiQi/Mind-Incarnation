import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.paths import ProjectPaths
from mi.thoughtdb import ThoughtDbStore


class TestThoughtDbNodesCli(unittest.TestCase):
    def test_node_create_list_show_retract(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            # Create.
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(
                    [
                        "--home",
                        str(home),
                        "node",
                        "create",
                        "--cd",
                        str(project_root),
                        "--scope",
                        "project",
                        "--type",
                        "decision",
                        "--title",
                        "Pick A",
                        "--text",
                        "Decision: pick A",
                        "--json",
                    ]
                )
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            payload = json.loads(out)
            nid = str(payload.get("node_id") or "")
            self.assertTrue(nid.startswith("nd_"))

            # List.
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "node", "list", "--cd", str(project_root), "--scope", "project", "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            items = json.loads(out)
            self.assertTrue(any(isinstance(n, dict) and n.get("node_id") == nid for n in items))

            # Show.
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "node", "show", nid, "--cd", str(project_root), "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            show = json.loads(out)
            self.assertEqual(show.get("node", {}).get("node_id"), nid)

            # Retract.
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "node", "retract", nid, "--cd", str(project_root), "--scope", "project"])
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            v = tdb.load_view(scope="project")
            self.assertEqual(v.node_status(nid), "retracted")


if __name__ == "__main__":
    unittest.main()
