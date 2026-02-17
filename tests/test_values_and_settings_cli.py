from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.paths import GlobalPaths, ProjectPaths
from mi.core.storage import iter_jsonl, now_rfc3339
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.operational_defaults import resolve_operational_defaults
from mi.thoughtdb.pins import ASK_WHEN_UNCERTAIN_TAG
from mi.thoughtdb.values import VALUES_BASE_TAG, VALUES_RAW_TAG, VALUES_SUMMARY_TAG


class TestValuesAndSettingsCli(unittest.TestCase):
    def test_values_set_no_compile_writes_values_set_event_and_raw_claim(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)

            # Capture stdout (values set prints ids).
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "values", "set", "--text", "Prefer fewer questions.", "--no-compile"])
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)

            gp = GlobalPaths(home_dir=home)
            rows = [x for x in iter_jsonl(gp.global_evidence_log_path) if isinstance(x, dict)]
            self.assertTrue(any(str(x.get("kind") or "") == "values_set" for x in rows))

            pp = ProjectPaths(home_dir=home, project_root=Path("."), _project_id="__global__")
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            v = tdb.load_view(scope="global")

            raw = []
            for c in v.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=now_rfc3339()):
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                if VALUES_RAW_TAG in {str(x).strip() for x in tags if str(x).strip()}:
                    raw.append(c)
            self.assertTrue(raw)

            # No compilation => no values summary node should be written.
            nodes = []
            for n in v.iter_nodes(include_inactive=False, include_aliases=False):
                tags = n.get("tags") if isinstance(n.get("tags"), list) else []
                if VALUES_SUMMARY_TAG in {str(x).strip() for x in tags if str(x).strip()}:
                    nodes.append(n)
            self.assertEqual(nodes, [])

            # No derived values:base claims without compilation/values_claim_patch.
            vals = []
            for c in v.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=now_rfc3339()):
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                if VALUES_BASE_TAG in {str(x).strip() for x in tags if str(x).strip()}:
                    vals.append(c)
            self.assertEqual(vals, [])

    def test_settings_set_global_and_project_override(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            # Global: set ask_when_uncertain=proceed (single-field update).
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code = mi_main(["--home", str(home), "settings", "set", "--ask-when-uncertain", "proceed"])
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code, 0)
            self.assertTrue(out.strip().startswith("{"))  # JSON result

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            op = resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339())
            self.assertFalse(op.ask_when_uncertain)

            # Project override: set ask_when_uncertain=ask (should win for this project).
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                code2 = mi_main(
                    [
                        "--home",
                        str(home),
                        "settings",
                        "set",
                        "--scope",
                        "project",
                        "--cd",
                        str(project_root),
                        "--ask-when-uncertain",
                        "ask",
                    ]
                )
                out2 = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            self.assertEqual(code2, 0)
            parsed = json.loads(out2)
            self.assertTrue(parsed.get("ok"))

            op2 = resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339())
            self.assertTrue(op2.ask_when_uncertain)

            # Sanity: project store now contains a tagged override claim.
            v_proj = tdb.load_view(scope="project")
            tagged = []
            for c in v_proj.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=now_rfc3339()):
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                if ASK_WHEN_UNCERTAIN_TAG in {str(x).strip() for x in tags if str(x).strip()}:
                    tagged.append(c)
            self.assertTrue(tagged)


if __name__ == "__main__":
    unittest.main()
