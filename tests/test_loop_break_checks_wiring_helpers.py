from __future__ import annotations

import unittest

from mi.runtime.wiring.loop_break_checks import (
    LoopBreakChecksWiringDeps,
    loop_break_get_checks_input_wired,
)


class LoopBreakChecksWiringHelpersTests(unittest.TestCase):
    def test_loop_break_get_checks_input_wired_plumbs_notes_and_deps(self) -> None:
        calls = {"plan": 0, "resolve": 0}

        def _get_check_input(obj):
            if not isinstance(obj, dict):
                return ""
            if not bool(obj.get("should_run_checks", False)):
                return ""
            return str(obj.get("hands_check_input") or "").strip()

        def _plan(**kwargs):
            calls["plan"] += 1
            self.assertEqual(kwargs.get("batch_id"), "b1.loop_break_checks")
            self.assertEqual(kwargs.get("tag"), "checks_loopbreak:b1")
            self.assertEqual(kwargs.get("thought_db_context"), {"a": 1})
            self.assertEqual(kwargs.get("repo_observation"), {"r": 1})
            self.assertTrue(kwargs.get("should_plan"))
            self.assertEqual(kwargs.get("notes_on_skipped"), "skippedX")
            self.assertEqual(kwargs.get("notes_on_error"), "errorY")
            return {"should_run_checks": True, "hands_check_input": "pytest -q"}, "ref", "ok"

        def _resolve(**kwargs):
            calls["resolve"] += 1
            self.assertEqual(kwargs.get("user_input_batch_id"), "b1.loop_break")
            return kwargs.get("checks_obj") or {}, ""

        deps = LoopBreakChecksWiringDeps(
            get_check_input=_get_check_input,
            plan_checks_and_record=_plan,
            resolve_tls_for_checks=_resolve,
            empty_check_plan=lambda: {"should_run_checks": False, "hands_check_input": ""},
            notes_on_skipped="skippedX",
            notes_on_error="errorY",
        )

        out, block = loop_break_get_checks_input_wired(
            base_batch_id="b1",
            hands_last_message="h",
            thought_db_context={"a": 1},
            repo_observation={"r": 1},
            existing_check_plan=None,
            deps=deps,
        )

        self.assertEqual(out, "pytest -q")
        self.assertEqual(block, "")
        self.assertEqual(calls, {"plan": 1, "resolve": 1})


if __name__ == "__main__":
    unittest.main()

