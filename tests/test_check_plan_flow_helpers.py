from __future__ import annotations

import unittest

from mi.runtime.autopilot.check_plan_flow import (
    CheckPlanFlowDeps,
    append_check_plan_record_with_tracking,
    call_plan_min_checks,
    plan_checks_and_record,
)


class CheckPlanFlowHelpersTests(unittest.TestCase):
    def _mk_deps(self, *, mind_call):
        persisted = {"n": 0}
        segment_items: list[dict[str, object]] = []
        evidence_items: list[dict[str, object]] = []

        def _evidence_append(rec: dict[str, object]):
            out = dict(rec)
            out["event_id"] = f"ev_{len(evidence_items) + 1}"
            evidence_items.append(out)
            return out

        deps = CheckPlanFlowDeps(
            empty_check_plan=lambda: {
                "should_run_checks": False,
                "hands_check_input": "",
                "needs_testless_strategy": False,
                "testless_strategy_question": "",
                "notes": "",
            },
            evidence_append=_evidence_append,
            segment_add=lambda item: segment_items.append(dict(item)),
            persist_segment_state=lambda: persisted.__setitem__("n", persisted["n"] + 1),
            now_ts=lambda: "2026-02-01T00:00:00Z",
            thread_id="t_1",
            plan_min_checks_prompt_builder=lambda **_kwargs: "plan-prompt",
            mind_call=mind_call,
        )
        return deps, evidence_items, segment_items, persisted

    def test_append_check_plan_record_tracks_window_and_segment(self) -> None:
        deps, evidence_items, segment_items, persisted = self._mk_deps(
            mind_call=lambda **_kwargs: ({}, "m1", "ok")
        )
        evidence_window = [{"kind": "x", "idx": i} for i in range(10)]

        rec = append_check_plan_record_with_tracking(
            batch_id="b1",
            checks_obj={"should_run_checks": True, "notes": "n"},
            mind_transcript_ref="mind_ref_1",
            evidence_window=evidence_window,
            deps=deps,
        )
        self.assertEqual(rec.get("event_id"), "ev_1")
        self.assertEqual(len(evidence_window), 8)
        self.assertEqual(evidence_window[-1].get("kind"), "check_plan")
        self.assertEqual(evidence_window[-1].get("batch_id"), "b1")
        self.assertEqual(evidence_window[-1].get("event_id"), "ev_1")
        self.assertEqual(len(evidence_items), 1)
        self.assertEqual(evidence_items[0].get("mind_transcript_ref"), "mind_ref_1")
        self.assertEqual(len(segment_items), 1)
        self.assertEqual(segment_items[0].get("event_id"), "ev_1")
        self.assertEqual(persisted["n"], 1)

    def test_call_plan_min_checks_fallback_for_skipped(self) -> None:
        deps, _evidence_items, _segment_items, _persisted = self._mk_deps(
            mind_call=lambda **_kwargs: (None, "mind_x", "skipped")
        )
        checks, ref, state = call_plan_min_checks(
            batch_id="b2",
            tag="checks_b2",
            task="t",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            recent_evidence=[],
            repo_observation={},
            notes_on_skipped="note skipped",
            notes_on_error="note error",
            deps=deps,
        )
        self.assertEqual(ref, "mind_x")
        self.assertEqual(state, "skipped")
        self.assertEqual(checks.get("notes"), "note skipped")

    def test_plan_checks_and_record_skip_still_records(self) -> None:
        deps, evidence_items, segment_items, persisted = self._mk_deps(
            mind_call=lambda **_kwargs: ({}, "m", "ok")
        )
        evidence_window: list[dict[str, object]] = []
        checks, ref, state = plan_checks_and_record(
            batch_id="b3",
            tag="checks_b3",
            task="t",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            recent_evidence=evidence_window,
            repo_observation={},
            should_plan=False,
            notes_on_skip="skip reason",
            notes_on_skipped="",
            notes_on_error="",
            evidence_window=evidence_window,
            postprocess=None,
            deps=deps,
        )
        self.assertEqual(ref, "")
        self.assertEqual(state, "skipped")
        self.assertEqual(checks.get("notes"), "skip reason")
        self.assertEqual(len(evidence_items), 1)
        self.assertEqual(len(segment_items), 1)
        self.assertEqual(persisted["n"], 1)

    def test_plan_checks_and_record_postprocess_applied(self) -> None:
        deps, _evidence_items, _segment_items, _persisted = self._mk_deps(
            mind_call=lambda **_kwargs: (
                {
                    "should_run_checks": True,
                    "hands_check_input": "run smoke",
                    "needs_testless_strategy": False,
                    "testless_strategy_question": "",
                    "notes": "orig",
                },
                "mind_ok",
                "ok",
            )
        )
        evidence_window: list[dict[str, object]] = []
        checks, ref, state = plan_checks_and_record(
            batch_id="b4",
            tag="checks_b4",
            task="t",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            recent_evidence=evidence_window,
            repo_observation={},
            should_plan=True,
            notes_on_skip="",
            notes_on_skipped="",
            notes_on_error="",
            evidence_window=evidence_window,
            postprocess=lambda obj, _state: {**obj, "notes": "patched"} if isinstance(obj, dict) else obj,
            deps=deps,
        )
        self.assertEqual(state, "ok")
        self.assertEqual(ref, "mind_ok")
        self.assertEqual(checks.get("notes"), "patched")


if __name__ == "__main__":
    unittest.main()
