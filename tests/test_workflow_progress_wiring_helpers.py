from __future__ import annotations

import unittest

from mi.runtime.wiring.workflow_progress import (
    WorkflowProgressWiringDeps,
    apply_workflow_progress_wired,
)


class WorkflowProgressWiringHelpersTests(unittest.TestCase):
    def test_apply_workflow_progress_wired_skips_without_active_workflow(self) -> None:
        calls: dict[str, object] = {"mind": 0, "evidence": 0, "write": 0}

        deps = WorkflowProgressWiringDeps(
            task="t",
            hands_provider="codex",
            runtime_cfg_getter=lambda: {},
            project_overlay={},
            workflow_run={},
            workflow_load_effective=lambda *_a, **_k: None,
            load_active_workflow=lambda **_kwargs: None,
            workflow_progress_prompt_builder=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not build prompt")),
            mind_call=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not call mind")),
            evidence_append=lambda _rec: calls.__setitem__("evidence", int(calls["evidence"]) + 1) or {},
            now_ts=lambda: "2026-02-01T00:00:00Z",
            thread_id_getter=lambda: "t1",
            apply_workflow_progress_output_fn=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not apply")),
            write_project_overlay=lambda _ov: calls.__setitem__("write", int(calls["write"]) + 1),
        )

        apply_workflow_progress_wired(
            batch_idx=1,
            batch_id="b1",
            summary={},
            evidence_obj={},
            repo_obs={},
            hands_last="last",
            tdb_ctx_batch_obj={},
            last_batch_input="input",
            deps=deps,
        )

        self.assertEqual(calls["evidence"], 0)
        self.assertEqual(calls["write"], 0)
        self.assertEqual(calls["mind"], 0)

    def test_apply_workflow_progress_wired_plumbs_and_persists(self) -> None:
        calls: dict[str, object] = {}
        evidence_events: list[dict[str, object]] = []
        wrote: list[dict[str, object]] = []

        def _prompt_builder(**kwargs: object) -> str:
            calls["prompt_kwargs"] = dict(kwargs)
            return "wf-prompt"

        def _mind_call(**kwargs: object):
            calls["mind_kwargs"] = dict(kwargs)
            return (
                {"completed_step_ids": ["s1"], "next_step_id": "s2"},
                "mind_ref",
                "ok",
            )

        def _evidence_append(rec: dict[str, object]):
            evidence_events.append(dict(rec))
            out = dict(rec)
            out["event_id"] = "ev_1"
            return out

        def _apply_output(**kwargs: object) -> bool:
            calls["apply_kwargs"] = dict(kwargs)
            return True

        project_overlay: dict[str, object] = {"overlay": True}
        workflow_run: dict[str, object] = {"active": True, "workflow_id": "wf_1"}

        deps = WorkflowProgressWiringDeps(
            task="task",
            hands_provider="codex",
            runtime_cfg_getter=lambda: {"runtime": True},
            project_overlay=project_overlay,  # mutated in-place for persistence
            workflow_run=workflow_run,
            workflow_load_effective=lambda wid=None: {"id": str(wid), "name": "WF"},
            load_active_workflow=lambda **_kwargs: {"id": "wf_1", "name": "WF"},
            workflow_progress_prompt_builder=_prompt_builder,
            mind_call=_mind_call,
            evidence_append=_evidence_append,
            now_ts=lambda: "2026-02-01T00:00:00Z",
            thread_id_getter=lambda: "t1",
            apply_workflow_progress_output_fn=_apply_output,
            write_project_overlay=lambda ov: wrote.append(dict(ov)),
        )

        apply_workflow_progress_wired(
            batch_idx=3,
            batch_id="b3",
            summary={"transcript_observation": {"paths": ["a.py"]}},
            evidence_obj={
                "facts": ["f1"],
                "actions": [],
                "results": [],
                "unknowns": ["u1"],
                "risk_signals": ["r1"],
            },
            repo_obs={"changed_files": ["a.py"]},
            hands_last="hands last",
            tdb_ctx_batch_obj={"claims": []},
            last_batch_input="last input",
            deps=deps,
        )

        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("task"), "task")
        self.assertEqual(prompt_kwargs.get("hands_provider"), "codex")
        self.assertEqual(prompt_kwargs.get("runtime_cfg"), {"runtime": True})
        self.assertEqual(prompt_kwargs.get("project_overlay"), project_overlay)
        self.assertEqual(prompt_kwargs.get("thought_db_context"), {"claims": []})
        self.assertEqual(prompt_kwargs.get("workflow"), {"id": "wf_1", "name": "WF"})
        self.assertEqual(prompt_kwargs.get("workflow_run"), workflow_run)
        self.assertEqual(prompt_kwargs.get("last_batch_input"), "last input")
        self.assertEqual(prompt_kwargs.get("hands_last_message"), "hands last")
        self.assertEqual(
            prompt_kwargs.get("latest_evidence"),
            {
                "batch_id": "b3",
                "facts": ["f1"],
                "actions": [],
                "results": [],
                "unknowns": ["u1"],
                "risk_signals": ["r1"],
                "repo_observation": {"changed_files": ["a.py"]},
                "transcript_observation": {"paths": ["a.py"]},
            },
        )

        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "workflow_progress.json")
        self.assertEqual(mind_kwargs.get("prompt"), "wf-prompt")
        self.assertEqual(mind_kwargs.get("tag"), "wf_progress_b3")
        self.assertEqual(mind_kwargs.get("batch_id"), "b3.workflow_progress")

        self.assertEqual(len(evidence_events), 1)
        self.assertEqual(evidence_events[0].get("kind"), "workflow_progress")
        self.assertEqual(evidence_events[0].get("batch_id"), "b3.workflow_progress")
        self.assertEqual(evidence_events[0].get("thread_id"), "t1")
        self.assertEqual(evidence_events[0].get("workflow_id"), "wf_1")
        self.assertEqual(evidence_events[0].get("workflow_name"), "WF")
        self.assertEqual(evidence_events[0].get("mind_transcript_ref"), "mind_ref")
        self.assertEqual((evidence_events[0].get("output") or {}).get("next_step_id"), "s2")

        apply_kwargs = calls.get("apply_kwargs")
        self.assertIsInstance(apply_kwargs, dict)
        self.assertEqual(apply_kwargs.get("active_workflow"), {"id": "wf_1", "name": "WF"})
        self.assertEqual(apply_kwargs.get("workflow_run"), workflow_run)
        self.assertEqual(apply_kwargs.get("wf_progress_output"), {"completed_step_ids": ["s1"], "next_step_id": "s2"})
        self.assertEqual(apply_kwargs.get("batch_id"), "b3")
        self.assertEqual(apply_kwargs.get("thread_id"), "t1")

        self.assertEqual(len(wrote), 1)
        self.assertIs(wrote[0].get("workflow_run"), workflow_run)


if __name__ == "__main__":
    unittest.main()

