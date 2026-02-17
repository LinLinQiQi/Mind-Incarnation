from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.paths import ProjectPaths
from mi.thoughtdb import ThoughtDbStore


class TestThoughtDbEdgesCli(unittest.TestCase):
    def test_edge_list_and_show(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            event_id = "ev_test_edge_cli_000001"
            cid = tdb.append_claim_create(
                claim_type="goal",
                text="Ship v1",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=[event_id],
                confidence=1.0,
                notes="",
            )

            # Create edges via CLI (covers EvidenceLog + append-only store write).
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(
                    [
                        "--home",
                        str(home),
                        "edge",
                        "create",
                        "--cd",
                        str(project_root),
                        "--scope",
                        "project",
                        "--type",
                        "depends_on",
                        "--from",
                        event_id,
                        "--to",
                        cid,
                        "--json",
                    ]
                )
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            eid_project = str(json.loads(out).get("edge_id") or "")

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(
                    [
                        "--home",
                        str(home),
                        "edge",
                        "create",
                        "--cd",
                        str(project_root),
                        "--scope",
                        "global",
                        "--type",
                        "depends_on",
                        "--from",
                        event_id,
                        "--to",
                        cid,
                        "--visibility",
                        "project",
                        "--json",
                    ]
                )
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            eid_global = str(json.loads(out).get("edge_id") or "")

            # List (project)
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "edge", "list", "--cd", str(project_root), "--scope", "project", "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            items = json.loads(out)
            self.assertTrue(any(isinstance(e, dict) and e.get("edge_id") == eid_project for e in items))

            # Filter by from/type
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(
                    [
                        "--home",
                        str(home),
                        "edge",
                        "list",
                        "--cd",
                        str(project_root),
                        "--scope",
                        "project",
                        "--type",
                        "depends_on",
                        "--from",
                        event_id,
                        "--json",
                    ]
                )
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            items = json.loads(out)
            self.assertTrue(all(isinstance(e, dict) and e.get("edge_type") == "depends_on" for e in items))
            self.assertTrue(all(isinstance(e, dict) and e.get("from_id") == event_id for e in items))

            # List effective should dedupe identical triples (project wins over global).
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "edge", "list", "--cd", str(project_root), "--scope", "effective", "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            items = json.loads(out)
            edge_ids = [e.get("edge_id") for e in items if isinstance(e, dict)]
            self.assertIn(eid_project, edge_ids)
            self.assertNotIn(eid_global, edge_ids)

            # Show by id (effective)
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "edge", "show", eid_project, "--cd", str(project_root), "--json"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(payload.get("edge", {}).get("edge_id"), eid_project)


if __name__ == "__main__":
    unittest.main()
