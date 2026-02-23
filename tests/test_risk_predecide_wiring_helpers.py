from __future__ import annotations

import unittest

from mi.runtime.autopilot.batch_effects import append_evidence_window
from mi.runtime.wiring.risk_predecide import (
    RiskEventRecordWiringDeps,
    RiskJudgeWiringDeps,
    append_risk_event_wired,
    query_risk_judge_wired,
)


class RiskPredecideWiringHelpersTests(unittest.TestCase):
    def test_query_risk_judge_wired_plumbs_runner_context(self) -> None:
        calls: dict[str, object] = {}

        def _recall(**kwargs: object) -> None:
            calls["recall_kwargs"] = dict(kwargs)

        def _prompt_builder(**kwargs: object) -> str:
            calls["prompt_kwargs"] = dict(kwargs)
            return "risk-prompt"

        def _mind_call(**kwargs: object):
            calls["mind_kwargs"] = dict(kwargs)
            return ({"category": "network", "severity": "high"}, "mind_ref", "ok")

        deps = RiskJudgeWiringDeps(
            task="ship it",
            hands_provider="codex",
            runtime_cfg_getter=lambda: {"runtime": True},
            project_overlay={"overlay": True},
            maybe_cross_project_recall=_recall,
            risk_judge_prompt_builder=_prompt_builder,
            mind_call=_mind_call,
            build_risk_fallback=lambda sig, state: {"category": "other", "severity": "low", "signals": sig, "state": state},
        )

        risk_obj, mind_ref = query_risk_judge_wired(
            batch_idx=2,
            batch_id="b2",
            risk_signals=["network", "install"],
            hands_last="doing curl",
            tdb_ctx_batch_obj={"claims": []},
            deps=deps,
        )

        self.assertEqual(mind_ref, "mind_ref")
        self.assertEqual(risk_obj.get("category"), "network")

        recall_kwargs = calls.get("recall_kwargs")
        self.assertIsInstance(recall_kwargs, dict)
        self.assertEqual(recall_kwargs.get("batch_id"), "b2.risk_recall")
        self.assertEqual(recall_kwargs.get("reason"), "risk_signal")
        self.assertEqual(recall_kwargs.get("query"), "network install\nship it")

        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("task"), "ship it")
        self.assertEqual(prompt_kwargs.get("hands_provider"), "codex")
        self.assertEqual(prompt_kwargs.get("runtime_cfg"), {"runtime": True})
        self.assertEqual(prompt_kwargs.get("project_overlay"), {"overlay": True})
        self.assertEqual(prompt_kwargs.get("thought_db_context"), {"claims": []})
        self.assertEqual(prompt_kwargs.get("risk_signals"), ["network", "install"])
        self.assertEqual(prompt_kwargs.get("hands_last_message"), "doing curl")

        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "risk_judge.json")
        self.assertEqual(mind_kwargs.get("prompt"), "risk-prompt")
        self.assertEqual(mind_kwargs.get("tag"), "risk_b2")
        self.assertEqual(mind_kwargs.get("batch_id"), "b2")

    def test_append_risk_event_wired_tracks_segment_and_persists(self) -> None:
        evidence_window: list[dict[str, object]] = []
        evidence_events: list[dict[str, object]] = []
        segment_written: list[dict[str, object]] = []
        persisted = {"n": 0}

        def _ev_append(rec: dict[str, object]):
            out = dict(rec)
            out["event_id"] = "ev_1"
            evidence_events.append(out)
            return out

        deps = RiskEventRecordWiringDeps(
            evidence_window=evidence_window,
            evidence_append=_ev_append,
            append_window=append_evidence_window,
            segment_add=lambda item: segment_written.append(dict(item)),
            persist_segment_state=lambda: persisted.__setitem__("n", int(persisted["n"]) + 1),
            now_ts=lambda: "2026-02-01T00:00:00Z",
            thread_id_getter=lambda: "t1",
        )

        out = append_risk_event_wired(
            batch_idx=4,
            risk_signals=["network"],
            risk_obj={"category": "network", "severity": "high"},
            risk_mind_ref="mind_risk",
            deps=deps,
        )

        self.assertEqual(out.get("event_id"), "ev_1")
        self.assertEqual(len(evidence_window), 1)
        self.assertEqual(evidence_window[0].get("kind"), "risk_event")
        self.assertEqual(evidence_window[0].get("event_id"), "ev_1")
        self.assertEqual(len(segment_written), 1)
        self.assertEqual(segment_written[0].get("kind"), "risk_event")
        self.assertEqual(persisted["n"], 1)


if __name__ == "__main__":
    unittest.main()

