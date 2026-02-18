from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from mi.cli import main as mi_main
from mi.core.paths import GlobalPaths


@contextmanager
def _chdir(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class TestProjectStatusCli(unittest.TestCase):
    def test_project_status_is_read_only_and_does_not_update_last(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            gp = GlobalPaths(home_dir=home)
            self.assertFalse(gp.project_selection_path.exists())

            with _chdir(proj):
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    code = mi_main(["--home", str(home), "project", "status", "--json"])
                    out = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(str(payload.get("project_root") or ""), str(proj.resolve()))
            self.assertEqual(str(payload.get("reason") or ""), "cwd")

            # Must not create/update the selection registry file.
            self.assertFalse(gp.project_selection_path.exists())

    def test_project_status_honors_here_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            with _chdir(proj):
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    code = mi_main(["--home", str(home), "--here", "project", "status", "--json"])
                    out = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(str(payload.get("project_root") or ""), str(proj.resolve()))
            self.assertEqual(str(payload.get("reason") or ""), "here")


if __name__ == "__main__":
    unittest.main()

