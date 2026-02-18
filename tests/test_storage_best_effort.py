from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path

from mi.core.storage import read_json_best_effort


@contextmanager
def _patched_env(changes: dict[str, str | None]):
    old = {k: os.environ.get(k) for k in changes.keys()}
    for k, v in changes.items():
        if v is None:
            if k in os.environ:
                del os.environ[k]
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                if k in os.environ:
                    del os.environ[k]
            else:
                os.environ[k] = v


class TestReadJsonBestEffort(unittest.TestCase):
    def test_invalid_json_with_warnings_does_not_print_by_default(self) -> None:
        with _patched_env({"MI_STATE_WARNINGS_STDERR": None}), tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{not json", encoding="utf-8")

            warnings: list[dict] = []
            buf = io.StringIO()
            with redirect_stderr(buf):
                out = read_json_best_effort(p, default={"ok": True}, label="x", warnings=warnings)

            self.assertEqual(out, {"ok": True})
            self.assertEqual(len(warnings), 1)
            self.assertEqual(str(warnings[0].get("label")), "x")
            self.assertEqual(buf.getvalue(), "")

    def test_env_force_print_even_with_warnings(self) -> None:
        with _patched_env({"MI_STATE_WARNINGS_STDERR": "1"}), tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{not json", encoding="utf-8")

            warnings: list[dict] = []
            buf = io.StringIO()
            with redirect_stderr(buf):
                out = read_json_best_effort(p, default=None, label="x", warnings=warnings)

            self.assertIsNone(out)
            self.assertEqual(len(warnings), 1)
            self.assertIn("quarantined and continued", buf.getvalue())

    def test_env_silence_even_without_warnings(self) -> None:
        with _patched_env({"MI_STATE_WARNINGS_STDERR": "0"}), tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{not json", encoding="utf-8")

            buf = io.StringIO()
            with redirect_stderr(buf):
                out = read_json_best_effort(p, default=None, label="x", warnings=None)

            self.assertIsNone(out)
            self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

