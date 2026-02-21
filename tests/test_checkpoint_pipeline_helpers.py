from __future__ import annotations

import unittest

from mi.runtime.autopilot.checkpoint_pipeline import (
    CheckpointPipelineDeps,
    run_checkpoint_pipeline,
)


class _Ctx:
    def to_prompt_obj(self) -> dict[str, object]:
        return {"ok": True}


class _Snap:
    def __init__(self) -> None:
        self.evidence_event = {"kind": "snapshot", "event_id": "ev_snap"}
        self.window_entry = {"kind": "snapshot", "snapshot_id": "sp_1"}


class CheckpointPipelineHelpersTests(unittest.TestCase):
    def test_duplicate_key_short_circuits(self) -> None:
        calls = {"mind": 0}

        def _mind_call(**kwargs):
            calls["mind"] += 1
            return {}, "", "ok"

        res = run_checkpoint_pipeline(
            checkpoint_enabled=True,
            segment_state={"segment_id": "seg_1", "records": []},
            segment_records=[],
            last_checkpoint_key="b1:not_done",
            batch_id="b1",
            planned_next_input="x",
            status_hint="not_done",
            note="n",
            thread_id="t1",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            evidence_window=[],
            deps=CheckpointPipelineDeps(
                build_decide_context=lambda **kwargs: _Ctx(),
                checkpoint_decide_prompt_builder=lambda **kwargs: "p",
                mind_call=_mind_call,
                evidence_append=lambda rec: rec,
                mine_workflow_from_segment=lambda **kwargs: None,
                mine_preferences_from_segment=lambda **kwargs: None,
                mine_claims_from_segment=lambda **kwargs: None,
                materialize_snapshot=lambda **kwargs: None,
                materialize_nodes_from_checkpoint=lambda **kwargs: None,
                new_segment_state=lambda **kwargs: {"records": []},
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
            ),
        )
        self.assertFalse(res.persist_segment_state)
        self.assertEqual(calls["mind"], 0)

    def test_non_checkpoint_persists_without_mining(self) -> None:
        calls = {"wf": 0, "pref": 0, "claim": 0}
        appended: list[dict[str, object]] = []

        def _append(rec):
            if isinstance(rec, dict):
                appended.append(rec)
                return rec
            return {}

        res = run_checkpoint_pipeline(
            checkpoint_enabled=True,
            segment_state={"segment_id": "seg_1", "records": []},
            segment_records=[{"kind": "evidence", "event_id": "ev_1"}],
            last_checkpoint_key="",
            batch_id="b1.extra",
            planned_next_input="next",
            status_hint="not_done",
            note="n",
            thread_id="tid_1",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            evidence_window=[],
            deps=CheckpointPipelineDeps(
                build_decide_context=lambda **kwargs: _Ctx(),
                checkpoint_decide_prompt_builder=lambda **kwargs: "p",
                mind_call=lambda **kwargs: ({"should_checkpoint": False}, "mref", "ok"),
                evidence_append=_append,
                mine_workflow_from_segment=lambda **kwargs: calls.__setitem__("wf", calls["wf"] + 1),
                mine_preferences_from_segment=lambda **kwargs: calls.__setitem__("pref", calls["pref"] + 1),
                mine_claims_from_segment=lambda **kwargs: calls.__setitem__("claim", calls["claim"] + 1),
                materialize_snapshot=lambda **kwargs: None,
                materialize_nodes_from_checkpoint=lambda **kwargs: None,
                new_segment_state=lambda **kwargs: {"records": []},
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
            ),
        )
        self.assertTrue(res.persist_segment_state)
        self.assertEqual(res.last_checkpoint_key, "b1:not_done")
        self.assertEqual(calls["wf"], 0)
        self.assertEqual(calls["pref"], 0)
        self.assertEqual(calls["claim"], 0)
        self.assertEqual(len(appended), 1)
        self.assertEqual(appended[0].get("kind"), "checkpoint")

    def test_checkpoint_runs_mining_snapshot_and_resets_segment(self) -> None:
        calls = {"wf": 0, "pref": 0, "claim": 0, "nodes": 0}
        window: list[dict[str, object]] = []

        def _append(rec):
            if isinstance(rec, dict):
                return rec
            return {}

        res = run_checkpoint_pipeline(
            checkpoint_enabled=True,
            segment_state={"segment_id": "seg_1", "records": [{"kind": "evidence"}]},
            segment_records=[{"kind": "evidence", "event_id": "ev_1"}],
            last_checkpoint_key="",
            batch_id="b2",
            planned_next_input="next",
            status_hint="not_done",
            note="checkpoint",
            thread_id="tid_1",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            evidence_window=window,
            deps=CheckpointPipelineDeps(
                build_decide_context=lambda **kwargs: _Ctx(),
                checkpoint_decide_prompt_builder=lambda **kwargs: "p",
                mind_call=lambda **kwargs: (
                    {
                        "should_checkpoint": True,
                        "should_mine_workflow": True,
                        "should_mine_preferences": False,
                        "checkpoint_kind": "phase_change",
                        "notes": "ok",
                    },
                    "mref",
                    "ok",
                ),
                evidence_append=_append,
                mine_workflow_from_segment=lambda **kwargs: calls.__setitem__("wf", calls["wf"] + 1),
                mine_preferences_from_segment=lambda **kwargs: calls.__setitem__("pref", calls["pref"] + 1),
                mine_claims_from_segment=lambda **kwargs: calls.__setitem__("claim", calls["claim"] + 1),
                materialize_snapshot=lambda **kwargs: _Snap(),
                materialize_nodes_from_checkpoint=lambda **kwargs: calls.__setitem__("nodes", calls["nodes"] + 1),
                new_segment_state=lambda **kwargs: {"records": [], "reason": kwargs.get("reason")},
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
            ),
        )
        self.assertTrue(res.persist_segment_state)
        self.assertEqual(calls["wf"], 1)
        self.assertEqual(calls["pref"], 0)
        self.assertEqual(calls["claim"], 1)
        self.assertEqual(calls["nodes"], 1)
        self.assertEqual(res.segment_state.get("reason"), "checkpoint:phase_change")
        self.assertEqual(len(window), 1)
        self.assertEqual(window[0].get("event_id"), "ev_snap")


if __name__ == "__main__":
    unittest.main()
