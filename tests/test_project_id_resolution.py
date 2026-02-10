import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mi.mindspec import MindSpecStore
from mi.paths import ProjectPaths, project_index_path


def _git(cwd: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class TestProjectIdResolution(unittest.TestCase):
    def test_project_id_stable_across_move_for_git_repo_even_without_index(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo_a = base / "repoA"
            repo_b = base / "repoB"
            repo_a.mkdir(parents=True, exist_ok=True)

            _git(repo_a, ["init"])
            _git(repo_a, ["config", "user.email", "mi@example.com"])
            _git(repo_a, ["config", "user.name", "MI"])
            _git(repo_a, ["remote", "add", "origin", "git@github.com:example/example.git"])

            (repo_a / "README.txt").write_text("x\n", encoding="utf-8")
            _git(repo_a, ["add", "."])
            _git(repo_a, ["commit", "-m", "init"])

            store = MindSpecStore(home_dir=home)

            # First run: creates overlay under legacy path-hash id, but writes identity fields.
            overlay_a = store.load_project_overlay(repo_a)
            pid_a = str(overlay_a.get("project_id") or "")
            ident_a = str(overlay_a.get("identity_key") or "")
            self.assertTrue(pid_a)
            self.assertTrue(ident_a.startswith("git:"))

            # Delete index to force the fallback scan path.
            idx = project_index_path(Path(home))
            if idx.exists():
                os.remove(idx)

            shutil.copytree(repo_a, repo_b)

            overlay_b = store.load_project_overlay(repo_b)
            pid_b = str(overlay_b.get("project_id") or "")
            ident_b = str(overlay_b.get("identity_key") or "")

            self.assertEqual(pid_a, pid_b)
            self.assertEqual(ident_a, ident_b)
            self.assertEqual(str(repo_b.resolve()), str(overlay_b.get("root_path") or ""))

            # Sanity: ProjectPaths resolves to the same id for the moved repo.
            pp_b = ProjectPaths(home_dir=Path(home), project_root=repo_b)
            self.assertEqual(pp_b.project_id, pid_a)


if __name__ == "__main__":
    unittest.main()
