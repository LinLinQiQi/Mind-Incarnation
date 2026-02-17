from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import (
    GlobalPaths,
    clear_pinned_project_selection,
    load_project_selection,
    list_project_aliases,
    record_last_project_selection,
    remove_project_alias,
    resolve_project_selection_token,
    set_pinned_project_selection,
    set_project_alias,
)


class TestProjectSelectionRegistry(unittest.TestCase):
    def test_roundtrip_last_pinned_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)

            last = record_last_project_selection(home, proj)
            self.assertEqual(str(last.get("root_path") or ""), str(proj.resolve()))

            pinned = set_pinned_project_selection(home, proj)
            self.assertEqual(str(pinned.get("root_path") or ""), str(proj.resolve()))

            aliased = set_project_alias(home, name="repo1", project_root=proj)
            self.assertEqual(str(aliased.get("root_path") or ""), str(proj.resolve()))

            aliases = list_project_aliases(home)
            self.assertIn("repo1", aliases)

            # Token resolution.
            self.assertEqual(resolve_project_selection_token(home, "@last"), proj.resolve())
            self.assertEqual(resolve_project_selection_token(home, "@pinned"), proj.resolve())
            self.assertEqual(resolve_project_selection_token(home, "@repo1"), proj.resolve())

            self.assertTrue(remove_project_alias(home, name="repo1"))
            self.assertNotIn("repo1", list_project_aliases(home))

            clear_pinned_project_selection(home)
            obj = load_project_selection(home)
            self.assertEqual(obj.get("pinned"), {})

    def test_corrupt_json_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td_home:
            home = Path(td_home)
            gp = GlobalPaths(home_dir=home)
            gp.project_selection_path.parent.mkdir(parents=True, exist_ok=True)
            gp.project_selection_path.write_text("{not json", encoding="utf-8")

            obj = load_project_selection(home)
            self.assertIsInstance(obj, dict)
            self.assertEqual(str(obj.get("version") or ""), "v1")

    def test_invalid_alias_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            proj = Path(td_proj)
            with self.assertRaises(ValueError):
                set_project_alias(home, name="bad name", project_root=proj)


if __name__ == "__main__":
    unittest.main()

