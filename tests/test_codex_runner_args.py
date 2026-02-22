from __future__ import annotations

import unittest
from pathlib import Path

from mi.providers.codex_runner import _append_common_exec_options, _build_codex_base_args


class TestCodexRunnerArgs(unittest.TestCase):
    def test_build_base_args(self) -> None:
        self.assertEqual(_build_codex_base_args(Path("repo")), ["codex", "--cd", "repo"])

    def test_append_common_exec_options_order(self) -> None:
        args = ["codex", "--cd", "repo", "exec"]
        _append_common_exec_options(
            args,
            skip_git_repo_check=True,
            full_auto=True,
            sandbox="read-only",
            output_schema_path=Path("schema.json"),
        )
        self.assertEqual(
            args,
            [
                "codex",
                "--cd",
                "repo",
                "exec",
                "--skip-git-repo-check",
                "--full-auto",
                "--sandbox",
                "read-only",
                "--json",
                "--output-schema",
                "schema.json",
            ],
        )

    def test_append_common_exec_options_minimal(self) -> None:
        args = ["codex", "--cd", "repo", "exec", "resume"]
        _append_common_exec_options(
            args,
            skip_git_repo_check=False,
            full_auto=False,
            sandbox=None,
            output_schema_path=None,
        )
        self.assertEqual(args, ["codex", "--cd", "repo", "exec", "resume", "--json"])


if __name__ == "__main__":
    unittest.main()

