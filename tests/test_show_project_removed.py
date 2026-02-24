from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from mi.cli_commands.show_tail import handle_show


class TestShowProjectRemoved(unittest.TestCase):
    def test_show_project_is_not_a_pseudo_ref_anymore(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            rc = handle_show(
                args=type("Args", (), {"ref": "project"})(),  # minimal namespace-ish object
                home_dir=Path("."),
                cfg={},
                resolve_project_root_from_args=lambda *a, **k: Path("."),
                effective_cd_arg=lambda _a: "",
                dispatch_fn=lambda *a, **k: 0,
            )
        self.assertEqual(rc, 2)
        self.assertIn("mi project show", err.getvalue())


if __name__ == "__main__":
    unittest.main()

