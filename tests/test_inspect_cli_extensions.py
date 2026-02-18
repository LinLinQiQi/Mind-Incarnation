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
from mi.thoughtdb import ThoughtDbStore


def _run_cli(argv: list[str]) -> tuple[int, str]:
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        code = mi_main(argv)
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return code, out


class TestInspectCliExtensions(unittest.TestCase):
    def test_claim_list_filters_and_graph_show(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            cid_now = tdb.append_claim_create(
                claim_type="preference",
                text="Prefer running pytest.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["a", "b"],
                source_event_ids=["ev_test_claim_now"],
                confidence=1.0,
                notes="",
            )

            cid_future = tdb.append_claim_create(
                claim_type="goal",
                text="Ship v2 someday.",
                scope="project",
                visibility="project",
                valid_from="2999-01-01T00:00:00Z",
                valid_to=None,
                tags=["future", "a"],
                source_event_ids=["ev_test_claim_future"],
                confidence=1.0,
                notes="",
            )

            cid_retracted = tdb.append_claim_create(
                claim_type="fact",
                text="This will be retracted.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["a"],
                source_event_ids=["ev_test_claim_retract"],
                confidence=1.0,
                notes="",
            )
            tdb.append_claim_retract(
                claim_id=cid_retracted,
                scope="project",
                rationale="test retract",
                source_event_ids=["ev_test_claim_retract"],
            )

            cid_old = tdb.append_claim_create(
                claim_type="preference",
                text="Old preference.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_claim_supersede"],
                confidence=1.0,
                notes="",
            )
            cid_new = tdb.append_claim_create(
                claim_type="preference",
                text="New preference.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_claim_supersede"],
                confidence=1.0,
                notes="",
            )
            tdb.append_edge(
                edge_type="supersedes",
                from_id=cid_old,
                to_id=cid_new,
                scope="project",
                visibility="project",
                source_event_ids=["ev_test_claim_supersede"],
                notes="",
            )

            # Default list filters by as-of=now, so the future claim is excluded.
            code, out = _run_cli(
                ["--home", str(home), "claim", "list", "--cd", str(project_root), "--scope", "project", "--json"]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {c.get("claim_id") for c in items if isinstance(c, dict)}
            self.assertIn(cid_now, ids)
            self.assertIn(cid_new, ids)
            self.assertNotIn(cid_future, ids)
            self.assertNotIn(cid_retracted, ids)
            self.assertNotIn(cid_old, ids)  # superseded => inactive by default

            # as-of far in the future should include the future claim.
            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "claim",
                    "list",
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--as-of",
                    "2999-01-02T00:00:00Z",
                    "--type",
                    "goal",
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {c.get("claim_id") for c in items if isinstance(c, dict)}
            self.assertIn(cid_future, ids)
            self.assertNotIn(cid_now, ids)

            # Tag filter (AND semantics): --tag a --tag b should keep only cid_now.
            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "claim",
                    "list",
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--tag",
                    "a",
                    "--tag",
                    "b",
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {c.get("claim_id") for c in items if isinstance(c, dict)}
            self.assertEqual(ids, {cid_now})

            # Status filter should auto-include inactive.
            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "claim",
                    "list",
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--status",
                    "retracted",
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {c.get("claim_id") for c in items if isinstance(c, dict)}
            self.assertIn(cid_retracted, ids)

            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "claim",
                    "list",
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--status",
                    "superseded",
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {c.get("claim_id") for c in items if isinstance(c, dict)}
            self.assertIn(cid_old, ids)

            # Graph show includes neighbors.
            cid_a = tdb.append_claim_create(
                claim_type="goal",
                text="Root claim.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_graph"],
                confidence=1.0,
                notes="",
            )
            cid_b = tdb.append_claim_create(
                claim_type="fact",
                text="Leaf claim.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_graph"],
                confidence=1.0,
                notes="",
            )
            tdb.append_edge(
                edge_type="depends_on",
                from_id=cid_a,
                to_id=cid_b,
                scope="project",
                visibility="project",
                source_event_ids=["ev_test_graph"],
                notes="",
            )

            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "claim",
                    "show",
                    cid_a,
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--json",
                    "--graph",
                    "--depth",
                    "1",
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads(out)
            graph = payload.get("graph")
            self.assertIsInstance(graph, dict)
            gclaims = graph.get("claims") if isinstance(graph, dict) else []
            gids = {c.get("claim_id") for c in gclaims if isinstance(c, dict)}
            self.assertIn(cid_a, gids)
            self.assertIn(cid_b, gids)
            gedges = graph.get("edges") if isinstance(graph, dict) else []
            self.assertTrue(any(isinstance(e, dict) and e.get("edge_type") == "depends_on" for e in gedges))

    def test_node_list_filters_and_graph_show(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            nid1 = tdb.append_node_create(
                node_type="decision",
                title="Pick A",
                text="Decision: pick A",
                scope="project",
                visibility="project",
                tags=["alpha", "beta"],
                source_event_ids=["ev_test_node1"],
                confidence=1.0,
                notes="",
            )
            nid2 = tdb.append_node_create(
                node_type="action",
                title="Do X",
                text="Run tests",
                scope="project",
                visibility="project",
                tags=["alpha"],
                source_event_ids=["ev_test_node2"],
                confidence=1.0,
                notes="",
            )

            nid3 = tdb.append_node_create(
                node_type="summary",
                title="Old summary",
                text="Will be retracted",
                scope="project",
                visibility="project",
                tags=[],
                source_event_ids=["ev_test_node3"],
                confidence=1.0,
                notes="",
            )
            tdb.append_node_retract(
                node_id=nid3,
                scope="project",
                rationale="test",
                source_event_ids=["ev_test_node3"],
            )

            # Type filter.
            code, out = _run_cli(
                ["--home", str(home), "node", "list", "--cd", str(project_root), "--scope", "project", "--type", "decision", "--json"]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {n.get("node_id") for n in items if isinstance(n, dict)}
            self.assertIn(nid1, ids)
            self.assertNotIn(nid2, ids)

            # Tag filter (AND semantics).
            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "node",
                    "list",
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--tag",
                    "alpha",
                    "--tag",
                    "beta",
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {n.get("node_id") for n in items if isinstance(n, dict)}
            self.assertEqual(ids, {nid1})

            # Status filter should auto-include inactive.
            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "node",
                    "list",
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--status",
                    "retracted",
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            items = json.loads(out)
            ids = {n.get("node_id") for n in items if isinstance(n, dict)}
            self.assertIn(nid3, ids)

            # Graph show.
            tdb.append_edge(
                edge_type="mentions",
                from_id=nid1,
                to_id=nid2,
                scope="project",
                visibility="project",
                source_event_ids=["ev_test_node_edge"],
                notes="",
            )
            code, out = _run_cli(
                [
                    "--home",
                    str(home),
                    "node",
                    "show",
                    nid1,
                    "--cd",
                    str(project_root),
                    "--scope",
                    "project",
                    "--json",
                    "--graph",
                    "--depth",
                    "1",
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads(out)
            graph = payload.get("graph")
            self.assertIsInstance(graph, dict)
            gnodes = graph.get("nodes") if isinstance(graph, dict) else []
            ids2 = {n.get("node_id") for n in gnodes if isinstance(n, dict)}
            self.assertIn(nid1, ids2)
            self.assertIn(nid2, ids2)

    def test_evidence_show_project_and_global(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)

            append_jsonl(
                pp.evidence_log_path,
                {"event_id": "ev_test_show_project", "kind": "hands_input", "input": "api_key=sk-test-1234567890"},
            )

            code, out = _run_cli(
                ["--home", str(home), "evidence", "show", "ev_test_show_project", "--cd", str(project_root), "--redact"]
            )
            self.assertEqual(code, 0)
            self.assertIn("ev_test_show_project", out)
            self.assertIn("[REDACTED]", out)

            gp = GlobalPaths(home_dir=home)
            append_jsonl(gp.global_evidence_log_path, {"event_id": "ev_test_show_global", "kind": "values_set", "text": "hi"})
            code, out = _run_cli(["--home", str(home), "evidence", "show", "ev_test_show_global", "--global"])
            self.assertEqual(code, 0)
            self.assertIn("ev_test_show_global", out)


if __name__ == "__main__":
    unittest.main()

