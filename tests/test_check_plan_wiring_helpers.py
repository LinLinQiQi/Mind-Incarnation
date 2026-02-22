from __future__ import annotations

import unittest

from mi.runtime.wiring.check_plan import CheckPlanWiringDeps, plan_checks_and_record_wired


class CheckPlanWiringHelpersTests(unittest.TestCase):
    def test_plan_checks_and_record_wired_plumbs_runner_context(self) -> None:
        calls: dict[str, object] = {}
        evidence_items: list[dict[str, object]] = []
        segment_items: list[dict[str, object]] = []
        persisted = {"n": 0}
        evidence_window: list[dict[str, object]] = []

        def _evidence_append(rec: dict[str, object]):
            out = dict(rec)
            out["event_id"] = f"ev_{len(evidence_items) + 1}"
            evidence_items.append(out)
            return out

        def _prompt_builder(**kwargs):
            calls["prompt_kwargs"] = dict(kwargs)
            return "plan-prompt"

        def _mind_call(**kwargs):
            calls["mind_kwargs"] = dict(kwargs)
            return (
                {
                    "should_run_checks": True,
                    "hands_check_input": "run smoke",
                    "needs_testless_strategy": False,
                    "testless_strategy_question": "",
                    "notes": "n",
                },
                "mind_ref_1",
                "ok",
            )

        deps = CheckPlanWiringDeps(
            task="t1",
            hands_provider="codex",
            mindspec_base_getter=lambda: {"x": 1},
            project_overlay={"o": 1},
            evidence_window=evidence_window,
            thread_id_getter=lambda: None,
            now_ts=lambda: "2026-02-01T00:00:00Z",
            evidence_append=_evidence_append,
            segment_add=lambda item: segment_items.append(dict(item)),
            persist_segment_state=lambda: persisted.__setitem__("n", persisted["n"] + 1),
            plan_min_checks_prompt_builder=_prompt_builder,
            mind_call=_mind_call,
            empty_check_plan=lambda: {
                "should_run_checks": False,
                "hands_check_input": "",
                "needs_testless_strategy": False,
                "testless_strategy_question": "",
                "notes": "",
            },
        )

        checks, ref, state = plan_checks_and_record_wired(
            batch_id="b1",
            tag="checks_b1",
            thought_db_context={"a": 1},
            repo_observation={"r": 1},
            should_plan=True,
            notes_on_skip="skip",
            notes_on_skipped="skipped",
            notes_on_error="error",
            postprocess=None,
            deps=deps,
        )

        self.assertEqual(state, "ok")
        self.assertEqual(ref, "mind_ref_1")
        self.assertEqual(checks.get("hands_check_input"), "run smoke")

        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("task"), "t1")
        self.assertEqual(prompt_kwargs.get("hands_provider"), "codex")
        self.assertEqual(prompt_kwargs.get("mindspec_base"), {"x": 1})
        self.assertEqual(prompt_kwargs.get("project_overlay"), {"o": 1})
        self.assertEqual(prompt_kwargs.get("thought_db_context"), {"a": 1})
        self.assertEqual(prompt_kwargs.get("repo_observation"), {"r": 1})

        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "plan_min_checks.json")
        self.assertEqual(mind_kwargs.get("prompt"), "plan-prompt")
        self.assertEqual(mind_kwargs.get("tag"), "checks_b1")
        self.assertEqual(mind_kwargs.get("batch_id"), "b1")

        # Ensure record-tracking side effects still occur.
        self.assertEqual(len(evidence_items), 1)
        self.assertEqual(evidence_items[0].get("kind"), "check_plan")
        self.assertEqual(len(segment_items), 1)
        self.assertEqual(persisted["n"], 1)


if __name__ == "__main__":
    unittest.main()
