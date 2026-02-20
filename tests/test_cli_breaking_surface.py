from __future__ import annotations

import unittest

from mi.cli import build_parser


class TestCliBreakingSurface(unittest.TestCase):
    def test_removed_top_level_alias_commands(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["ls", "claims"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["edit", "wf_x"])

    def test_removed_config_doctor_alias(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["config", "doctor"])

    def test_canonical_commands_still_parse(self) -> None:
        parser = build_parser()
        ns = parser.parse_args(["claim", "list"])
        self.assertEqual(ns.cmd, "claim")
        self.assertEqual(ns.claim_cmd, "list")


if __name__ == "__main__":
    unittest.main()
