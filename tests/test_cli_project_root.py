import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mi.paths import project_identity, resolve_cli_project_root, project_index_path


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


if __name__ == "__main__":
    unittest.main()

