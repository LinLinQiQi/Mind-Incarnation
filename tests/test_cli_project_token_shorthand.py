from __future__ import annotations

import unittest

from mi.cli import _rewrite_cli_argv, build_parser


class TestCliProjectTokenShorthand(unittest.TestCase):
    def test_rewrites_first_positional_project_token(self) -> None:
        self.assertEqual(_rewrite_cli_argv(["@pinned", "status"]), ["-C", "@pinned", "status"])

    def test_rewrites_after_global_home_option(self) -> None:
        self.assertEqual(
            _rewrite_cli_argv(["--home", "/tmp/mi-home", "@last", "status"]),
            ["--home", "/tmp/mi-home", "-C", "@last", "status"],
        )

    def test_does_not_rewrite_when_first_positional_is_command(self) -> None:
        self.assertEqual(_rewrite_cli_argv(["status"]), ["status"])

    def test_rewritten_argv_parses_as_global_cd(self) -> None:
        ns = build_parser().parse_args(_rewrite_cli_argv(["@repo1", "status"]))
        self.assertEqual(ns.cmd, "status")
        self.assertEqual(ns.global_cd, "@repo1")


if __name__ == "__main__":
    unittest.main()

