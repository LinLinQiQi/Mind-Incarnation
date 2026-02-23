from __future__ import annotations

import unittest

from mi.runtime.autopilot.claim_mining_flow import ClaimMiningDeps, mine_claims_from_segment


class _Ctx:
    def __init__(self, obj: dict[str, object]) -> None:
        self._obj = dict(obj)

    def to_prompt_obj(self) -> dict[str, object]:
        return dict(self._obj)


class ClaimMiningFlowHelpersTests(unittest.TestCase):
    def test_disabled_or_empty_no_calls(self) -> None:
        calls = {"mind": 0, "evidence": 0, "apply": 0}

        mine_claims_from_segment(
            enabled=False,
            executed_batches=1,
            max_claims=6,
            min_confidence=0.9,
            seg_evidence=[{"event_id": "ev_1"}],
            base_batch_id="b1",
            source="s",
            status="not_done",
            notes="n",
            task="task",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            thread_id="t1",
            segment_id="seg_1",
            deps=ClaimMiningDeps(
                build_decide_context=lambda **_kwargs: _Ctx({"claims": []}),
                mine_claims_prompt_builder=lambda **_kwargs: "p",
                mind_call=lambda **_kwargs: (calls.__setitem__("mind", calls["mind"] + 1) or (None, "", "ok")),
                apply_mined_output=lambda **_kwargs: calls.__setitem__("apply", calls["apply"] + 1) or {},
                evidence_append=lambda _rec: calls.__setitem__("evidence", calls["evidence"] + 1) or {},
                now_ts=lambda: "2026-01-01T00:00:00Z",
            ),
        )

        self.assertEqual(calls, {"mind": 0, "evidence": 0, "apply": 0})

    def test_mines_and_applies_with_allowed_event_ids(self) -> None:
        calls: dict[str, object] = {"mind": 0, "evidence": [], "apply": []}

        def _mind_call(**kwargs):
            calls["mind"] = int(calls["mind"]) + 1
            # ensure prompt builder wiring includes allowed_event_ids
            self.assertEqual(kwargs.get("schema_filename"), "mine_claims.json")
            return {"claims": [{"text": "x"}]}, "mind_ref", "ok"

        def _apply(**kwargs):
            calls["apply"].append(dict(kwargs))
            return {"written": ["c1"], "skipped": []}

        def _evidence(rec):
            calls["evidence"].append(dict(rec))
            return rec

        seg = [{"event_id": "ev_1"}, {"event_id": "ev_1"}, {"event_id": " ev_2 "}, {"event_id": ""}]

        mine_claims_from_segment(
            enabled=True,
            executed_batches=2,
            max_claims=6,
            min_confidence=0.9,
            seg_evidence=seg,
            base_batch_id="b7",
            source="checkpoint",
            status="not_done",
            notes="n",
            task="task",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            thread_id="t1",
            segment_id="seg_1",
            deps=ClaimMiningDeps(
                build_decide_context=lambda **_kwargs: _Ctx({"claims": []}),
                mine_claims_prompt_builder=lambda **kwargs: (
                    self.assertEqual(kwargs.get("allowed_event_ids"), ["ev_1", "ev_2"]) or "p"
                ),
                mind_call=_mind_call,
                apply_mined_output=_apply,
                evidence_append=_evidence,
                now_ts=lambda: "2026-01-01T00:00:00Z",
            ),
        )

        self.assertEqual(int(calls["mind"]), 1)
        self.assertEqual(len(calls["apply"]), 1)
        self.assertEqual(set(calls["apply"][0].get("allowed_event_ids") or []), {"ev_1", "ev_2"})
        ev = (calls["evidence"][-1] if calls["evidence"] else {})
        self.assertEqual(ev.get("kind"), "claim_mining")
        self.assertEqual(ev.get("batch_id"), "b7.claim_mining")
        self.assertEqual(ev.get("mind_transcript_ref"), "mind_ref")
        self.assertEqual((ev.get("config") or {}).get("max_claims_per_checkpoint"), 6)
        self.assertEqual((ev.get("applied") or {}).get("written"), ["c1"])


if __name__ == "__main__":
    unittest.main()

