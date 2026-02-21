from __future__ import annotations

import unittest

from mi.runtime.autopilot.loop_break_checks_flow import LoopBreakChecksDeps, loop_break_get_checks_input


class LoopBreakChecksFlowHelpersTests(unittest.TestCase):
    def test_existing_check_input_short_circuits(self) -> None:
        calls = {"plan": 0, "resolve": 0}

        def _get_check_input(obj):
            if not isinstance(obj, dict):
                return ""
            if not bool(obj.get("should_run_checks", False)):
                return ""
            return str(obj.get("hands_check_input") or "").strip()

        out, block = loop_break_get_checks_input(
            base_batch_id="b1",
            hands_last_message="h",
            thought_db_context={},
            repo_observation={},
            existing_check_plan={"should_run_checks": True, "hands_check_input": "pytest -q"},
            notes_on_skipped="skipped",
            notes_on_error="error",
            deps=LoopBreakChecksDeps(
                get_check_input=_get_check_input,
                plan_checks_and_record=lambda **_kwargs: calls.__setitem__("plan", calls["plan"] + 1) or ({}, "", "ok"),
                resolve_tls_for_checks=lambda **_kwargs: calls.__setitem__("resolve", calls["resolve"] + 1) or ({}, ""),
                empty_check_plan=lambda: {"should_run_checks": False, "hands_check_input": ""},
            ),
        )

        self.assertEqual(out, "pytest -q")
        self.assertEqual(block, "")
        self.assertEqual(calls, {"plan": 0, "resolve": 0})

    def test_plans_then_resolves_tls(self) -> None:
        calls = {"plan": 0, "resolve": 0}

        def _plan(**kwargs):
            calls["plan"] += 1
            self.assertTrue(kwargs.get("should_plan"))
            self.assertEqual(kwargs.get("batch_id"), "b2.loop_break_checks")
            self.assertEqual(kwargs.get("tag"), "checks_loopbreak:b2")
            self.assertEqual(kwargs.get("notes_on_skipped"), "skipped")
            self.assertEqual(kwargs.get("notes_on_error"), "error")
            return {"should_run_checks": True, "hands_check_input": "pytest -q"}, "ref", "ok"

        def _resolve(**kwargs):
            calls["resolve"] += 1
            self.assertEqual(kwargs.get("user_input_batch_id"), "b2.loop_break")
            self.assertEqual(kwargs.get("notes_prefix"), "loop_break")
            return kwargs.get("checks_obj") or {}, ""

        out, block = loop_break_get_checks_input(
            base_batch_id="b2",
            hands_last_message="h",
            thought_db_context={},
            repo_observation={},
            existing_check_plan=None,
            notes_on_skipped="skipped",
            notes_on_error="error",
            deps=LoopBreakChecksDeps(
                get_check_input=lambda obj: str((obj or {}).get("hands_check_input") or "").strip()
                if bool((obj or {}).get("should_run_checks", False))
                else "",
                plan_checks_and_record=_plan,
                resolve_tls_for_checks=_resolve,
                empty_check_plan=lambda: {"should_run_checks": False, "hands_check_input": ""},
            ),
        )

        self.assertEqual(out, "pytest -q")
        self.assertEqual(block, "")
        self.assertEqual(calls, {"plan": 1, "resolve": 1})

    def test_block_reason_propagates(self) -> None:
        out, block = loop_break_get_checks_input(
            base_batch_id="b3",
            hands_last_message="h",
            thought_db_context={},
            repo_observation={},
            existing_check_plan=None,
            notes_on_skipped="skipped",
            notes_on_error="error",
            deps=LoopBreakChecksDeps(
                get_check_input=lambda _obj: "",
                plan_checks_and_record=lambda **_kwargs: ({}, "", "ok"),
                resolve_tls_for_checks=lambda **_kwargs: ({}, "need user input"),
                empty_check_plan=lambda: {"should_run_checks": False, "hands_check_input": ""},
            ),
        )
        self.assertEqual(out, "")
        self.assertEqual(block, "need user input")


if __name__ == "__main__":
    unittest.main()

