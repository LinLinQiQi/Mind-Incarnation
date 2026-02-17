from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mi.core.paths import GlobalPaths, project_identity, resolve_cli_project_root, project_index_path


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class TestCliProjectRootResolution(unittest.TestCase):
    def test_infers_git_toplevel_when_cd_omitted_and_cwd_not_known(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            sub = repo / "sub"
            repo.mkdir(parents=True, exist_ok=True)
            sub.mkdir(parents=True, exist_ok=True)

            _git(repo, ["init"])
            _git(repo, ["config", "user.email", "mi@example.com"])
            _git(repo, ["config", "user.name", "MI"])

            # Even if a last-used selection exists, being inside git should prefer git toplevel.
            other = base / "other"
            other.mkdir(parents=True, exist_ok=True)
            gp = GlobalPaths(home_dir=Path(home))
            gp.project_selection_path.parent.mkdir(parents=True, exist_ok=True)
            gp.project_selection_path.write_text(
                json.dumps({"version": "v1", "last": {"root_path": str(other.resolve())}}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            root, reason = resolve_cli_project_root(Path(home), "", cwd=sub)
            self.assertEqual(root, repo.resolve())
            self.assertEqual(reason, "git_toplevel")

    def test_prefers_known_cwd_root_when_present_in_index(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            sub = repo / "sub"
            repo.mkdir(parents=True, exist_ok=True)
            sub.mkdir(parents=True, exist_ok=True)

            _git(repo, ["init"])
            _git(repo, ["config", "user.email", "mi@example.com"])
            _git(repo, ["config", "user.name", "MI"])

            ident_sub = project_identity(sub)
            key_sub = str(ident_sub.get("key") or "")
            idx_path = project_index_path(Path(home))
            idx_path.parent.mkdir(parents=True, exist_ok=True)
            idx_path.write_text(json.dumps({"version": "v1", "by_identity": {key_sub: "p_sub"}}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            root, reason = resolve_cli_project_root(Path(home), "", cwd=sub)
            self.assertEqual(root, sub.resolve())
            self.assertEqual(reason, "known:cwd")

    def test_env_override_mi_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as td:
            base = Path(td)
            a = base / "a"
            a.mkdir(parents=True, exist_ok=True)

            old = os.environ.get("MI_PROJECT_ROOT")
            os.environ["MI_PROJECT_ROOT"] = str(a)
            try:
                root, reason = resolve_cli_project_root(Path(home), "", cwd=base)
            finally:
                if old is None:
                    del os.environ["MI_PROJECT_ROOT"]
                else:
                    os.environ["MI_PROJECT_ROOT"] = old

            self.assertEqual(root, a.resolve())
            self.assertEqual(reason, "env:MI_PROJECT_ROOT")

    def test_cd_token_alias_resolves_from_selection_registry(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as td:
            base = Path(td)
            a = base / "a"
            a.mkdir(parents=True, exist_ok=True)

            gp = GlobalPaths(home_dir=Path(home))
            gp.project_selection_path.parent.mkdir(parents=True, exist_ok=True)
            gp.project_selection_path.write_text(
                json.dumps({"version": "v1", "aliases": {"foo": {"root_path": str(a.resolve())}}}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            root, reason = resolve_cli_project_root(Path(home), "@foo", cwd=base)
            self.assertEqual(root, a.resolve())
            self.assertEqual(reason, "arg:@foo")

    def test_cd_token_missing_returns_error_reason(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root, reason = resolve_cli_project_root(Path(home), "@missing", cwd=base)
            self.assertEqual(root, base.resolve())
            self.assertEqual(reason, "error:alias_missing:@missing")

    def test_outside_git_falls_back_to_last_when_cd_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as td:
            base = Path(td)
            a = base / "a"
            a.mkdir(parents=True, exist_ok=True)

            gp = GlobalPaths(home_dir=Path(home))
            gp.project_selection_path.parent.mkdir(parents=True, exist_ok=True)
            gp.project_selection_path.write_text(
                json.dumps({"version": "v1", "last": {"root_path": str(a.resolve())}}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            root, reason = resolve_cli_project_root(Path(home), "", cwd=base)
            self.assertEqual(root, a.resolve())
            self.assertEqual(reason, "last")


if __name__ == "__main__":
    unittest.main()
