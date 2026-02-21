from __future__ import annotations

import unittest

from mi.runtime.autopilot.checkpoint_mining import (
    PreferenceMiningDeps,
    WorkflowMiningDeps,
    mine_preferences_from_segment,
    mine_workflow_from_segment,
)


class _Ctx:
    def to_prompt_obj(self) -> dict[str, object]:
        return {"ok": True}


class CheckpointMiningHelpersTests(unittest.TestCase):
    def test_workflow_mining_can_solidify_and_sync(self) -> None:
        evidences: list[dict[str, object]] = []
        workflows: list[dict[str, object]] = []
        store = {"by_signature": {}}
        sync_calls = {"n": 0}

        def _mind_call(**kwargs):
            return (
                {
                    "should_suggest": True,
                    "suggestion": {
                        "signature": "sig_1",
                        "benefit": "high",
                        "confidence": 0.93,
                        "reason": "useful",
                        "workflow": {"name": "W", "trigger": {"mode": "keyword", "pattern": "x"}, "steps": []},
                    },
                },
                "mref",
                "ok",
            )

        deps = WorkflowMiningDeps(
            build_decide_context=lambda **kwargs: _Ctx(),
            suggest_workflow_prompt_builder=lambda **kwargs: "prompt",
            mind_call=_mind_call,
            evidence_append=lambda rec: evidences.append(rec) or rec,
            load_workflow_candidates=lambda: store,
            write_workflow_candidates=lambda obj: store.update(obj),
            flush_state_warnings=lambda: None,
            write_workflow=lambda wf: workflows.append(wf),
            new_workflow_id=lambda: "wf_new",
            enabled_effective_workflows=lambda: [{"id": "wf_new", "enabled": True}],
            sync_hosts=lambda wfs: sync_calls.__setitem__("n", sync_calls["n"] + 1) or {"ok": True, "count": len(wfs)},
            now_ts=lambda: "2026-01-01T00:00:00Z",
        )

        counted: set[str] = set()
        mine_workflow_from_segment(
            enabled=True,
            executed_batches=1,
            wf_cfg={"allow_single_if_high_benefit": True, "min_occurrences": 2, "auto_enable": True, "auto_sync_on_change": True},
            seg_evidence=[{"kind": "evidence", "event_id": "ev_1"}],
            base_batch_id="b1",
            source="checkpoint",
            status="not_done",
            notes="n",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thread_id="tid_1",
            wf_sigs_counted_in_run=counted,
            deps=deps,
        )

        kinds = [str(x.get("kind") or "") for x in evidences if isinstance(x, dict)]
        self.assertIn("workflow_suggestion", kinds)
        self.assertIn("workflow_solidified", kinds)
        self.assertIn("host_sync", kinds)
        self.assertEqual(len(workflows), 1)
        self.assertEqual(workflows[0].get("id"), "wf_new")
        self.assertEqual(sync_calls["n"], 1)

    def test_preference_mining_can_emit_solidified(self) -> None:
        evidences: list[dict[str, object]] = []
        store = {"by_signature": {}}
        learn_calls = {"n": 0}

        def _mind_call(**kwargs):
            return (
                {
                    "suggestions": [
                        {
                            "scope": "project",
                            "text": "prefer smoke first",
                            "benefit": "high",
                            "confidence": 0.91,
                            "rationale": "stable habit",
                        }
                    ]
                },
                "mref",
                "ok",
            )

        def _handle_learn_suggested(**kwargs):
            learn_calls["n"] += 1
            return ["cl_1"]

        deps = PreferenceMiningDeps(
            build_decide_context=lambda **kwargs: _Ctx(),
            mine_preferences_prompt_builder=lambda **kwargs: "prompt",
            mind_call=_mind_call,
            evidence_append=lambda rec: evidences.append(rec) or rec,
            load_preference_candidates=lambda: store,
            write_preference_candidates=lambda obj: store.update(obj),
            flush_state_warnings=lambda: None,
            existing_signature_map=lambda scope: {},
            claim_signature_fn=lambda **kwargs: "sig_claim",
            preference_signature_fn=lambda **kwargs: "sig_pref",
            handle_learn_suggested=_handle_learn_suggested,
            now_ts=lambda: "2026-01-01T00:00:00Z",
        )

        counted: set[str] = set()
        mine_preferences_from_segment(
            enabled=True,
            executed_batches=1,
            pref_cfg={"allow_single_if_high_benefit": True, "min_occurrences": 2, "min_confidence": 0.75, "max_suggestions": 3},
            seg_evidence=[{"kind": "evidence", "event_id": "ev_1"}],
            base_batch_id="b1",
            source="checkpoint",
            status="not_done",
            notes="n",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thread_id="tid_1",
            project_id="p1",
            pref_sigs_counted_in_run=counted,
            deps=deps,
        )

        kinds = [str(x.get("kind") or "") for x in evidences if isinstance(x, dict)]
        self.assertIn("preference_mining", kinds)
        self.assertIn("preference_solidified", kinds)
        self.assertEqual(learn_calls["n"], 1)
        by_sig = store.get("by_signature") if isinstance(store.get("by_signature"), dict) else {}
        self.assertIn("sig_pref", by_sig)
        self.assertEqual(by_sig["sig_pref"].get("applied_claim_ids"), ["cl_1"])

    def test_mining_disabled_is_noop(self) -> None:
        evidences: list[dict[str, object]] = []
        deps = WorkflowMiningDeps(
            build_decide_context=lambda **kwargs: _Ctx(),
            suggest_workflow_prompt_builder=lambda **kwargs: "prompt",
            mind_call=lambda **kwargs: ({}, "", "ok"),
            evidence_append=lambda rec: evidences.append(rec) or rec,
            load_workflow_candidates=lambda: {"by_signature": {}},
            write_workflow_candidates=lambda obj: None,
            flush_state_warnings=lambda: None,
            write_workflow=lambda wf: None,
            new_workflow_id=lambda: "wf_new",
            enabled_effective_workflows=lambda: [],
            sync_hosts=lambda wfs: {"ok": True},
            now_ts=lambda: "2026-01-01T00:00:00Z",
        )
        mine_workflow_from_segment(
            enabled=False,
            executed_batches=1,
            wf_cfg={},
            seg_evidence=[],
            base_batch_id="b1",
            source="checkpoint",
            status="not_done",
            notes="n",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thread_id="tid_1",
            wf_sigs_counted_in_run=set(),
            deps=deps,
        )
        self.assertEqual(evidences, [])


if __name__ == "__main__":
    unittest.main()
