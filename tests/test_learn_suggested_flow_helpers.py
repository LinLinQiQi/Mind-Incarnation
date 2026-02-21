from __future__ import annotations

import unittest

from mi.runtime.autopilot.learn_suggested_flow import LearnSuggestedDeps, apply_learn_suggested


class LearnSuggestedFlowHelpersTests(unittest.TestCase):
    def test_apply_with_auto_learn_false_logs_without_new_claims(self) -> None:
        written: list[dict[str, object]] = []

        deps = LearnSuggestedDeps(
            claim_signature_fn=lambda **kwargs: "sig_1",
            existing_signature_map=lambda scope: {},
            append_claim_create=lambda **kwargs: "cl_new",
            evidence_append=lambda rec: written.append(rec) or rec,
            now_ts=lambda: "2026-01-01T00:00:00Z",
            new_suggestion_id=lambda: "ls_1",
            project_id="p1",
            thread_id="tid_1",
        )
        applied, rec = apply_learn_suggested(
            learn_suggested=[{"scope": "project", "text": "prefer smoke first", "rationale": "r"}],
            batch_id="b1",
            source="risk_judge",
            mind_transcript_ref="mref",
            source_event_ids=["ev_1"],
            runtime_cfg={"violation_response": {"auto_learn": False}},
            deps=deps,
        )
        self.assertEqual(applied, [])
        self.assertIsInstance(rec, dict)
        self.assertEqual(written[0].get("kind"), "learn_suggested")
        self.assertFalse(bool(written[0].get("auto_learn")))

    def test_apply_reuses_existing_signature_and_dedups(self) -> None:
        written: list[dict[str, object]] = []

        deps = LearnSuggestedDeps(
            claim_signature_fn=lambda **kwargs: "sig_existing",
            existing_signature_map=lambda scope: {"sig_existing": "cl_existing"},
            append_claim_create=lambda **kwargs: "cl_new",
            evidence_append=lambda rec: written.append(rec) or rec,
            now_ts=lambda: "2026-01-01T00:00:00Z",
            new_suggestion_id=lambda: "ls_2",
            project_id="p1",
            thread_id="tid_1",
        )
        applied, rec = apply_learn_suggested(
            learn_suggested=[{"scope": "project", "text": "prefer smoke first", "rationale": "r"}],
            batch_id="b2",
            source="decide_next",
            mind_transcript_ref="mref",
            source_event_ids=["ev_1"],
            runtime_cfg={"violation_response": {"auto_learn": True}},
            deps=deps,
        )
        self.assertEqual(applied, ["cl_existing"])
        self.assertIsInstance(rec, dict)
        self.assertEqual((rec or {}).get("applied_claim_ids"), ["cl_existing"])

    def test_apply_writes_new_claim_when_enabled(self) -> None:
        written: list[dict[str, object]] = []
        claim_calls = {"n": 0}

        def _append_claim_create(**kwargs):
            claim_calls["n"] += 1
            return f"cl_{claim_calls['n']}"

        deps = LearnSuggestedDeps(
            claim_signature_fn=lambda **kwargs: f"sig_{kwargs.get('scope')}",
            existing_signature_map=lambda scope: {},
            append_claim_create=_append_claim_create,
            evidence_append=lambda rec: written.append(rec) or rec,
            now_ts=lambda: "2026-01-01T00:00:00Z",
            new_suggestion_id=lambda: "ls_3",
            project_id="p1",
            thread_id="tid_1",
        )
        applied, rec = apply_learn_suggested(
            learn_suggested=[
                {"scope": "project", "text": "prefer smoke first", "rationale": "r1"},
                {"scope": "global", "text": "prefer explicit checklists", "rationale": "r2"},
            ],
            batch_id="b3",
            source="mine_preferences",
            mind_transcript_ref="mref",
            source_event_ids=["ev_1", "ev_2"],
            runtime_cfg={"violation_response": {"auto_learn": True}},
            deps=deps,
        )
        self.assertEqual(len(applied), 2)
        self.assertEqual(claim_calls["n"], 2)
        self.assertIsInstance(rec, dict)
        self.assertEqual((rec or {}).get("id"), "ls_3")


if __name__ == "__main__":
    unittest.main()
