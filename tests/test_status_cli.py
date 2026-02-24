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


class TestStatusCli(unittest.TestCase):
    def test_status_is_read_only_and_does_not_update_project_selection(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            gp = GlobalPaths(home_dir=home)
            self.assertFalse(gp.project_selection_path.exists())

            with _chdir(proj):
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    code = mi_main(["--home", str(home), "status", "--json"])
                    out = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(str(payload.get("project_root") or ""), str(proj.resolve()))
            next_steps = payload.get("next_steps") if isinstance(payload.get("next_steps"), list) else []
            # Copy/pasteable: project-scoped suggestions include the resolved project_root.
            self.assertTrue(any(str(proj.resolve()) in str(x) for x in next_steps))

            # status must not create/update the selection registry file.
            self.assertFalse(gp.project_selection_path.exists())

    def test_status_honors_here_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            with _chdir(proj):
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    code = mi_main(["--home", str(home), "--here", "status", "--json"])
                    out = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(str(payload.get("project_root") or ""), str(proj.resolve()))
            self.assertEqual(str(payload.get("reason") or ""), "here")


if __name__ == "__main__":
    unittest.main()
