from __future__ import annotations

import unittest

from mi.runtime.autopilot.workflow_progress_flow import (
    WorkflowProgressQueryDeps,
    apply_workflow_progress_and_persist,
    append_workflow_progress_event,
    build_workflow_progress_latest_evidence,
    query_workflow_progress,
)


class WorkflowProgressFlowHelpersTests(unittest.TestCase):
    def test_build_latest_evidence_normalizes_fields(self) -> None:
        out = build_workflow_progress_latest_evidence(
            batch_id="b3",
            summary={"transcript_observation": {"last": "ok"}},
            evidence_obj={
                "facts": [{"k": "f"}],
                "actions": [{"k": "a"}],
                "results": [{"k": "r"}],
                "unknowns": [{"k": "u"}],
                "risk_signals": ["network_write"],
            },
            repo_obs={"status": "dirty"},
        )
        self.assertEqual(out["batch_id"], "b3")
        self.assertEqual(len(out["facts"]), 1)
        self.assertEqual(len(out["actions"]), 1)
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(len(out["unknowns"]), 1)
        self.assertEqual(out["risk_signals"], ["network_write"])
        self.assertEqual(out["repo_observation"], {"status": "dirty"})
        self.assertEqual(out["transcript_observation"], {"last": "ok"})

    def test_query_workflow_progress_uses_deps_and_normalizes(self) -> None:
        seen: dict[str, object] = {}

        def _prompt_builder(**kwargs):
            seen["prompt_kwargs"] = kwargs
            return "wf-prompt"

        def _mind_call(**kwargs):
            seen["mind_kwargs"] = kwargs
            return {"should_update": True}, "mind_1", "ok"

        out, ref, state = query_workflow_progress(
            batch_idx=7,
            batch_id="b7",
            task="Refactor parser",
            hands_provider="codex",
            mindspec_base={"ask": "auto"},
            project_overlay={"rules": []},
            active_workflow={"id": "wf1"},
            workflow_run={"active": True},
            latest_evidence={"facts": []},
            last_batch_input="do next",
            hands_last_message="done",
            thought_db_context={"claims": []},
            deps=WorkflowProgressQueryDeps(
                workflow_progress_prompt_builder=_prompt_builder,
                mind_call=_mind_call,
            ),
        )
        self.assertEqual(out.get("should_update"), True)
        self.assertEqual(ref, "mind_1")
        self.assertEqual(state, "ok")
        self.assertEqual(seen["mind_kwargs"]["schema_filename"], "workflow_progress.json")
        self.assertEqual(seen["mind_kwargs"]["tag"], "wf_progress_b7")
        self.assertEqual(seen["mind_kwargs"]["batch_id"], "b7.workflow_progress")
        self.assertEqual(seen["mind_kwargs"]["prompt"], "wf-prompt")
        self.assertEqual(seen["prompt_kwargs"]["task"], "Refactor parser")
        self.assertEqual(seen["prompt_kwargs"]["hands_provider"], "codex")

    def test_append_workflow_progress_event_writes_expected_record(self) -> None:
        written: list[dict[str, object]] = []

        def _append_event(rec):
            rec2 = dict(rec)
            rec2["event_id"] = "ev_1"
            written.append(rec2)
            return rec2

        rec = append_workflow_progress_event(
            batch_id="b2",
            thread_id="tid_1",
            active_workflow={"id": "wf2", "name": "Ship"},
            wf_prog_obj={"should_update": False},
            wf_prog_ref="mref_2",
            wf_prog_state="ok",
            evidence_append=_append_event,
            now_ts=lambda: "2026-02-01T00:00:00Z",
        )
        self.assertEqual(rec.get("event_id"), "ev_1")
        self.assertEqual(written[0]["kind"], "workflow_progress")
        self.assertEqual(written[0]["workflow_id"], "wf2")
        self.assertEqual(written[0]["workflow_name"], "Ship")
        self.assertEqual(written[0]["mind_transcript_ref"], "mref_2")

    def test_apply_and_persist_only_when_changed(self) -> None:
        persisted: list[str] = []

        def _persist():
            persisted.append("saved")

        changed = apply_workflow_progress_and_persist(
            batch_id="b1",
            thread_id="tid",
            active_workflow={"id": "wf"},
            workflow_run={},
            wf_prog_obj={"should_update": True},
            apply_workflow_progress_output_fn=lambda **_kwargs: True,
            persist_overlay=_persist,
            now_ts=lambda: "2026-02-01T00:00:00Z",
        )
        self.assertTrue(changed)
        self.assertEqual(persisted, ["saved"])

        persisted.clear()
        changed2 = apply_workflow_progress_and_persist(
            batch_id="b1",
            thread_id="tid",
            active_workflow={"id": "wf"},
            workflow_run={},
            wf_prog_obj={"should_update": False},
            apply_workflow_progress_output_fn=lambda **_kwargs: False,
            persist_overlay=_persist,
            now_ts=lambda: "2026-02-01T00:00:00Z",
        )
        self.assertFalse(changed2)
        self.assertEqual(persisted, [])


if __name__ == "__main__":
    unittest.main()
